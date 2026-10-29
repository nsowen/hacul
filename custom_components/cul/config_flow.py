# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Config flow for the CUL integration."""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from homeassistant.components import usb
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from homeassistant.helpers.service_info.usb import UsbServiceInfo
from pycul import Cul, CulError
from pycul.protocols.max import DeviceType, MaxDevice, normalize_address
import serial.tools.list_ports
import voluptuous as vol

from .const import (
    CONF_ADOPT_DEVICES,
    CONF_BASE_TEMPERATURE,
    CONF_BAUDRATE,
    CONF_DEFAULT_TEMPERATURE,
    CONF_EXTERNAL,
    CONF_FAKE_SC_ADDRESS,
    CONF_FAKE_WT_ADDRESS,
    CONF_FAST_SEND,
    CONF_MAX_ADDRESS,
    CONF_NAME,
    CONF_PROTOCOL,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULES,
    CONF_SEND_INTERVAL,
    CONF_STICKS,
    CONF_TEMPERATURE_SENSOR,
    CONF_URL,
    CONF_WINDOW_SENSOR,
    DEFAULT_BASE_TEMPERATURE,
    DEFAULT_BAUDRATE,
    DEFAULT_FAKE_SC_ADDRESS,
    DEFAULT_FAKE_WT_ADDRESS,
    DEFAULT_MAX_ADDRESS,
    DEFAULT_SCHEDULE_TEMPERATURE,
    DEFAULT_SEND_INTERVAL,
    DOMAIN,
    PROTOCOL_MAX,
)

_LOGGER = logging.getLogger(__name__)

CONF_THERMOSTAT = "thermostat"
CONF_ZONE = "zone"
CONF_DEVICES = "devices"
CONF_GROUP = "group"
CONF_LINK = "link"
CONF_SHARED_CONTACTS = "shared_window_contacts"
CONF_ALLOW_SEVERAL_WALL = "allow_several_wall_thermostats"

#: Devices that belong to a heating zone (eco switches and plug adapters do not).
ZONE_DEVICE_TYPES = (
    DeviceType.HEATING_THERMOSTAT,
    DeviceType.HEATING_THERMOSTAT_PLUS,
    DeviceType.WALL_THERMOSTAT,
    DeviceType.SHUTTER_CONTACT,
)


class ZoneCandidate(NamedTuple):
    """A suggested heating zone."""

    label: str
    members: list[str]
    #: Group id the members already use, if the suggestion comes from it.
    group: int | None
    #: Window contacts linked with several zones of the suggestion.
    shared: list[str]


def split_by_wall_thermostat(
    members: list[str], devices: dict[str, MaxDevice]
) -> tuple[list[list[str]], list[str]]:
    """Split devices with several wall thermostats into one zone per wall thermostat.

    A large room can have several heating zones, each with its own wall
    thermostat. Radiator thermostats join the wall thermostat they are linked
    with, window contacts the zone whose thermostats they are linked with;
    contacts linked with several zones are returned as shared. Devices without
    known links form one more zone. With fewer than two wall thermostats the
    devices stay one zone.
    """
    walls = [a for a in members if devices[a].device_type is DeviceType.WALL_THERMOSTAT]
    if len(walls) < 2:
        return [members], []

    def linked(a: str, b: str) -> bool:
        return b in devices[a].link_partners or a in devices[b].link_partners

    zones = [
        {wall}
        | {
            a
            for a in members
            if devices[a].device_type.is_thermostat and a not in walls and linked(a, wall)
        }
        for wall in walls
    ]
    shared: list[str] = []
    for contact in members:
        if devices[contact].device_type is not DeviceType.SHUTTER_CONTACT:
            continue
        hits = [zone for zone in zones if any(linked(contact, t) for t in zone)]
        if len(hits) == 1:
            hits[0].add(contact)
        elif len(hits) > 1:
            shared.append(contact)
    assigned = set().union(*zones, shared)
    result = [sorted(zone) for zone in zones]
    if rest := [a for a in members if a not in assigned]:
        result.append(rest)
    return result, shared


BAUDRATES = [9600, 38400, 57600, 115200]

_TEMPERATURE_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=4.5, max=30.5, step=0.5, unit_of_measurement="°C", mode=NumberSelectorMode.BOX
    )
)


class CannotConnect(Exception):
    """The stick did not answer like a culfw device."""


async def validate_stick(url: str, baudrate: int) -> str:
    """Open the stick, read its firmware version and close it again."""
    cul = Cul(url, baudrate, reconnect=False)
    try:
        await cul.connect()
    except CulError as err:
        raise CannotConnect(str(err)) from err
    try:
        assert cul.version is not None
        return cul.version.raw
    finally:
        await cul.close()


async def _serial_port_options(hass: HomeAssistant) -> list[SelectOptionDict]:
    ports = await hass.async_add_executor_job(serial.tools.list_ports.comports)
    options: list[SelectOptionDict] = []
    for port in ports:
        path = await hass.async_add_executor_job(usb.get_serial_by_id, port.device)
        label = usb.human_readable_device_name(
            port.device, port.serial_number, port.manufacturer, port.description, port.vid, port.pid
        )
        options.append(SelectOptionDict(value=path, label=label))
    return options


def _stick_schema(ports: list[SelectOptionDict], defaults: dict[str, Any]) -> dict[Any, Any]:
    return {
        vol.Required(CONF_URL, default=defaults.get(CONF_URL, vol.UNDEFINED)): SelectSelector(
            SelectSelectorConfig(options=ports, custom_value=True, mode=SelectSelectorMode.DROPDOWN)
        ),
        vol.Required(CONF_BAUDRATE, default=defaults.get(CONF_BAUDRATE, DEFAULT_BAUDRATE)): vol.In(
            BAUDRATES
        ),
        vol.Optional(CONF_NAME, default=defaults.get(CONF_NAME, vol.UNDEFINED)): TextSelector(),
    }


def _address(value: str) -> str:
    try:
        return normalize_address(value)
    except ValueError as err:
        raise vol.Invalid("invalid_address") from err


class CulConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a MAX! network with its first stick."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CulOptionsFlow:
        """Return the options flow."""
        return CulOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose the stick and the MAX! controller address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_create(user_input, errors)
            if result is not None:
                return result

        ports = await _serial_port_options(self.hass)
        schema = vol.Schema(
            {
                **_stick_schema(ports, user_input or self._discovered),
                vol.Required(
                    CONF_MAX_ADDRESS,
                    default=(user_input or {}).get(CONF_MAX_ADDRESS, DEFAULT_MAX_ADDRESS),
                ): TextSelector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_usb(self, discovery_info: UsbServiceInfo) -> ConfigFlowResult:
        """Handle a busware CUL plugged in."""
        path = await self.hass.async_add_executor_job(usb.get_serial_by_id, discovery_info.device)
        for entry in self._async_current_entries(include_ignore=True):
            if any(stick[CONF_URL] == path for stick in entry.data.get(CONF_STICKS, [])):
                return self.async_abort(reason="already_configured")
        await self.async_set_unique_id(f"usb_{discovery_info.serial_number or path}")
        self._abort_if_unique_id_configured()
        self._discovered = {CONF_URL: path, CONF_BAUDRATE: DEFAULT_BAUDRATE}
        self.context["title_placeholders"] = {
            "name": usb.human_readable_device_name(
                path,
                discovery_info.serial_number,
                discovery_info.manufacturer,
                discovery_info.description,
                discovery_info.vid,
                discovery_info.pid,
            )
        }
        return await self.async_step_usb_confirm()

    async def async_step_usb_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered stick and ask for the controller address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_create({**self._discovered, **user_input}, errors)
            if result is not None:
                return result
        return self.async_show_form(
            step_id="usb_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_MAX_ADDRESS, default=DEFAULT_MAX_ADDRESS): TextSelector()}
            ),
            description_placeholders={CONF_URL: self._discovered[CONF_URL]},
            errors=errors,
        )

    async def _async_create(
        self, user_input: dict[str, Any], errors: dict[str, str]
    ) -> ConfigFlowResult | None:
        try:
            address = _address(user_input[CONF_MAX_ADDRESS])
        except vol.Invalid:
            errors[CONF_MAX_ADDRESS] = "invalid_address"
            return None
        if address in (DEFAULT_FAKE_WT_ADDRESS, DEFAULT_FAKE_SC_ADDRESS, "000000"):
            errors[CONF_MAX_ADDRESS] = "reserved_address"
            return None
        # One config entry per MAX! network, identified by its controller address.
        await self.async_set_unique_id(address, raise_on_progress=False)
        self._abort_if_unique_id_configured()

        url = user_input[CONF_URL]
        try:
            version = await validate_stick(url, user_input[CONF_BAUDRATE])
        except CannotConnect as err:
            _LOGGER.debug("Cannot connect to %s: %s", url, err)
            errors["base"] = "cannot_connect"
            return None
        _LOGGER.info("Found %s on %s", version, url)
        stick = {
            CONF_URL: url,
            CONF_BAUDRATE: user_input[CONF_BAUDRATE],
            CONF_NAME: user_input.get(CONF_NAME) or url.rsplit("/", 1)[-1],
        }
        return self.async_create_entry(
            title=f"MAX! {address}",
            data={
                CONF_PROTOCOL: PROTOCOL_MAX,
                CONF_MAX_ADDRESS: address,
                CONF_FAKE_WT_ADDRESS: DEFAULT_FAKE_WT_ADDRESS,
                CONF_FAKE_SC_ADDRESS: DEFAULT_FAKE_SC_ADDRESS,
                CONF_STICKS: [stick],
            },
        )


class CulOptionsFlow(OptionsFlow):
    """Sticks, behaviour, week profile sources and external sensors."""

    def __init__(self) -> None:
        self._thermostat: str | None = None
        self._zone: list[str] = []
        self._zone_group: int | None = None
        self._zone_shared: list[str] = []
        self._ask_several_wall = False

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the menu."""
        menu = ["settings", "add_stick", "zone_select", "schedule_select", "external_select"]
        if len(self.config_entry.data[CONF_STICKS]) > 1:
            menu.insert(2, "remove_stick")
        return self.async_show_menu(step_id="init", menu_options=menu)

    # -------------------------------------------------------------- settings

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """General behaviour of the MAX! network."""
        if user_input is not None:
            return self.async_create_entry(data={**self.config_entry.options, **user_input})
        options = self.config_entry.options
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ADOPT_DEVICES, default=options.get(CONF_ADOPT_DEVICES, True)
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_FAST_SEND, default=options.get(CONF_FAST_SEND, False)
                    ): BooleanSelector(),
                }
            ),
        )

    # ---------------------------------------------------------------- sticks

    async def async_step_add_stick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add another stick to the same MAX! network."""
        errors: dict[str, str] = {}
        sticks = list(self.config_entry.data[CONF_STICKS])
        if user_input is not None:
            url = user_input[CONF_URL]
            name = user_input.get(CONF_NAME) or url.rsplit("/", 1)[-1]
            if any(s[CONF_URL] == url or s[CONF_NAME] == name for s in sticks):
                errors["base"] = "stick_exists"
            else:
                try:
                    await validate_stick(url, user_input[CONF_BAUDRATE])
                except CannotConnect:
                    errors["base"] = "cannot_connect"
            if not errors:
                sticks.append(
                    {CONF_URL: url, CONF_BAUDRATE: user_input[CONF_BAUDRATE], CONF_NAME: name}
                )
                return self._save_sticks(sticks)
        ports = await _serial_port_options(self.hass)
        return self.async_show_form(
            step_id="add_stick",
            data_schema=vol.Schema(_stick_schema(ports, user_input or {})),
            errors=errors,
        )

    async def async_step_remove_stick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove a stick (at least one stays)."""
        sticks = list(self.config_entry.data[CONF_STICKS])
        if user_input is not None:
            return self._save_sticks([s for s in sticks if s[CONF_NAME] != user_input[CONF_NAME]])
        return self.async_show_form(
            step_id="remove_stick",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=s[CONF_NAME], label=f"{s[CONF_NAME]} ({s[CONF_URL]})"
                                )
                                for s in sticks
                            ]
                        )
                    )
                }
            ),
        )

    def _save_sticks(self, sticks: list[dict[str, Any]]) -> ConfigFlowResult:
        self.hass.config_entries.async_update_entry(
            self.config_entry, data={**self.config_entry.data, CONF_STICKS: sticks}
        )
        return self.async_create_entry(data=dict(self.config_entry.options))

    # ----------------------------------------------------------------- zones

    def _zone_candidates(self) -> dict[str, ZoneCandidate]:
        """Suggest heating zones: from HA areas, known MAX! groups and learned links."""
        hub = self.config_entry.runtime_data
        devices = {a: d for a, d in hub.devices.items() if d.device_type in ZONE_DEVICE_TYPES}
        candidates: dict[str, ZoneCandidate] = {}

        def names(addresses: list[str]) -> str:
            return ", ".join(hub.device_name(a) for a in addresses)

        registry = dr.async_get(self.hass)
        areas = ar.async_get(self.hass)
        by_area: dict[str, list[str]] = {}
        for address in devices:
            entry = registry.async_get_device(identifiers={(DOMAIN, address)})
            if entry and entry.area_id:
                by_area.setdefault(entry.area_id, []).append(address)
        for area_id, members in sorted(by_area.items()):
            area = areas.async_get_area(area_id)
            area_name = area.name if area else area_id
            zones, shared = split_by_wall_thermostat(sorted(members), devices)
            for index, zone in enumerate(zones, 1):
                label = f"{area_name}: {names(zone)}"
                if len(zones) > 1:
                    label = f"{area_name} ({index}/{len(zones)}): {names(zone)}"
                candidates[f"area:{area_id}:{index}"] = ZoneCandidate(label, zone, None, shared)

        groups: dict[int, list[str]] = {}
        for address, device in devices.items():
            if device.group:
                groups.setdefault(device.group, []).append(address)
        for group, members in sorted(groups.items()):
            candidates[f"group:{group}"] = ZoneCandidate(
                f"MAX! {group}: {names(members)}", sorted(members), group, []
            )

        grouped = [set(members) for members in groups.values()]
        for cluster in hub.protocol.link_clusters():
            members = sorted(cluster & devices.keys())
            if len(members) < 2 or any(set(members) <= g for g in grouped):
                continue
            zones, shared = split_by_wall_thermostat(members, devices)
            for zone in zones:
                candidates[f"linked:{','.join(zone)}"] = ZoneCandidate(
                    names(zone), zone, None, shared
                )
        return candidates

    async def async_step_zone_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the heating zone to set up, or start from scratch."""
        candidates = self._zone_candidates()
        if user_input is not None or not candidates:
            choice = candidates.get((user_input or {}).get(CONF_ZONE, ""))
            if choice is not None:
                self._zone = choice.members
                self._zone_group = choice.group
                self._zone_shared = choice.shared
            return await self.async_step_zone()
        return self.async_show_form(
            step_id="zone_select",
            data_schema=vol.Schema(
                {
                    # Left empty: choose the devices on the next page.
                    vol.Optional(CONF_ZONE): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=value, label=candidate.label)
                                for value, candidate in candidates.items()
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    def _default_group(self) -> int:
        hub = self.config_entry.runtime_data
        if self._zone_group is not None:
            return self._zone_group
        groups = {hub.devices[a].group for a in self._zone if a in hub.devices}
        if len(groups) == 1 and (group := groups.pop()):
            return group  # the devices already share a group
        used = {d.group for d in hub.devices.values()}
        return next(g for g in range(1, 256) if g not in used)

    async def async_step_zone(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Assign the group id and link the devices of one heating zone."""
        hub = self.config_entry.runtime_data
        errors: dict[str, str] = {}
        if user_input is not None:
            addresses = user_input.get(CONF_DEVICES, [])
            group = int(user_input[CONF_GROUP])
            shared = [a for a in user_input.get(CONF_SHARED_CONTACTS, []) if a not in addresses]
            walls = [
                a for a in addresses if hub.devices[a].device_type is DeviceType.WALL_THERMOSTAT
            ]
            self._zone, self._zone_group, self._zone_shared = addresses, group, shared
            if not addresses:
                errors[CONF_DEVICES] = "no_devices"
            elif group and len(walls) > 1 and not user_input.get(CONF_ALLOW_SEVERAL_WALL):
                # Allowed, but they would overrule each other: ask once more.
                self._ask_several_wall = True
                errors["base"] = "several_wall_thermostats"
            else:
                self.config_entry.async_create_background_task(
                    self.hass,
                    hub.async_group_devices(addresses, group, user_input[CONF_LINK], shared),
                    f"{DOMAIN} zone {group}",
                )
                return self.async_abort(
                    reason="zone_started", description_placeholders={"group": str(group)}
                )

        def device_options(types: tuple[DeviceType, ...]) -> list[SelectOptionDict]:
            return sorted(
                (
                    SelectOptionDict(value=address, label=f"{hub.device_name(address)} ({address})")
                    for address, device in hub.devices.items()
                    if device.device_type in types
                ),
                key=lambda o: o["label"],
            )

        options = device_options(ZONE_DEVICE_TYPES)
        if not options:
            return self.async_abort(reason="no_devices")
        schema: dict[Any, Any] = {
            vol.Required(CONF_DEVICES, default=self._zone): SelectSelector(
                SelectSelectorConfig(options=options, multiple=True)
            ),
            vol.Required(CONF_GROUP, default=self._default_group()): NumberSelector(
                NumberSelectorConfig(min=0, max=255, step=1, mode=NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_LINK, default=True): BooleanSelector(),
        }
        contacts = device_options((DeviceType.SHUTTER_CONTACT,))
        if contacts:
            schema[vol.Optional(CONF_SHARED_CONTACTS, default=self._zone_shared)] = SelectSelector(
                SelectSelectorConfig(options=contacts, multiple=True)
            )
        if self._ask_several_wall:
            schema[vol.Required(CONF_ALLOW_SEVERAL_WALL, default=False)] = BooleanSelector()
        return self.async_show_form(step_id="zone", data_schema=vol.Schema(schema), errors=errors)

    # ----------------------------------------------------------- thermostats

    def _thermostat_options(self) -> list[SelectOptionDict]:
        registry = dr.async_get(self.hass)
        options = []
        for address, device in self.config_entry.runtime_data.devices.items():
            if not device.device_type.is_thermostat:
                continue
            entry = registry.async_get_device(identifiers={(DOMAIN, address)})
            name = (entry.name_by_user or entry.name) if entry else None
            options.append(SelectOptionDict(value=address, label=f"{name or address} ({address})"))
        return sorted(options, key=lambda o: o["label"])

    async def _select_thermostat(
        self, step_id: str, next_step: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._thermostat = user_input[CONF_THERMOSTAT]
            return await getattr(self, f"async_step_{next_step}")()
        options = self._thermostat_options()
        if not options:
            return self.async_abort(reason="no_thermostats")
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_THERMOSTAT): SelectSelector(
                        SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    async def async_step_schedule_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the thermostat whose week profile comes from a schedule helper."""
        return await self._select_thermostat("schedule_select", "schedule", user_input)

    async def async_step_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Link a schedule helper; leave it empty to unlink."""
        assert self._thermostat is not None
        schedules = dict(self.config_entry.options.get(CONF_SCHEDULES, {}))
        if user_input is not None:
            if user_input.get(CONF_SCHEDULE_ENTITY):
                schedules[self._thermostat] = user_input
            else:
                schedules.pop(self._thermostat, None)
            return self.async_create_entry(
                data={**self.config_entry.options, CONF_SCHEDULES: schedules}
            )
        current = schedules.get(self._thermostat, {})
        return self.async_show_form(
            step_id="schedule",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCHEDULE_ENTITY,
                        description={"suggested_value": current.get(CONF_SCHEDULE_ENTITY)},
                    ): EntitySelector(EntitySelectorConfig(domain="schedule")),
                    vol.Required(
                        CONF_BASE_TEMPERATURE,
                        default=current.get(CONF_BASE_TEMPERATURE, DEFAULT_BASE_TEMPERATURE),
                    ): _TEMPERATURE_SELECTOR,
                    vol.Required(
                        CONF_DEFAULT_TEMPERATURE,
                        default=current.get(CONF_DEFAULT_TEMPERATURE, DEFAULT_SCHEDULE_TEMPERATURE),
                    ): _TEMPERATURE_SELECTOR,
                }
            ),
            description_placeholders={"thermostat": self._thermostat},
        )

    async def async_step_external_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the thermostat that gets values from HA sensors."""
        return await self._select_thermostat("external_select", "external", user_input)

    async def async_step_external(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Assign a room temperature sensor and/or a window sensor."""
        assert self._thermostat is not None
        external = dict(self.config_entry.options.get(CONF_EXTERNAL, {}))
        if user_input is not None:
            if user_input.get(CONF_TEMPERATURE_SENSOR) or user_input.get(CONF_WINDOW_SENSOR):
                external[self._thermostat] = user_input
            else:
                external.pop(self._thermostat, None)
            return self.async_create_entry(
                data={**self.config_entry.options, CONF_EXTERNAL: external}
            )
        current = external.get(self._thermostat, {})
        return self.async_show_form(
            step_id="external",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_TEMPERATURE_SENSOR,
                        description={"suggested_value": current.get(CONF_TEMPERATURE_SENSOR)},
                    ): EntitySelector(
                        EntitySelectorConfig(domain="sensor", device_class="temperature")
                    ),
                    vol.Optional(
                        CONF_WINDOW_SENSOR,
                        description={"suggested_value": current.get(CONF_WINDOW_SENSOR)},
                    ): EntitySelector(
                        EntitySelectorConfig(
                            domain="binary_sensor", device_class=["window", "door", "opening"]
                        )
                    ),
                    vol.Required(
                        CONF_SEND_INTERVAL,
                        default=current.get(CONF_SEND_INTERVAL, DEFAULT_SEND_INTERVAL),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=120,
                            max=3600,
                            step=60,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            description_placeholders={"thermostat": self._thermostat},
        )
