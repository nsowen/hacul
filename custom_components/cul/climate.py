# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Climate entities for MAX! radiator and wall thermostats.

Mapping of the MAX! modes:
- AUTO: the device follows its week profile. Setting a temperature in AUTO
  keeps AUTO and holds the temperature until the next switch point.
- HEAT: manual (or temporary) set point.
- OFF: set point 4.5 degree, the device's "off".
- Preset boost: the valve opens for the configured boost duration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    PRESET_BOOST,
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_HALVES, UnitOfTemperature
from homeassistant.core import HomeAssistant, ServiceResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
import homeassistant.util.dt as dt_util
from pycul.protocols.max import DayProfile, MaxDevice, Mode, WeekProfile
from pycul.protocols.max.weekprofile import WEEKDAY_NAMES

from . import CulConfigEntry
from .const import DEFAULT_BASE_TEMPERATURE, DOMAIN, signal_device_added, signal_zones_changed
from .entity import (
    CulDeviceEntity,
    CulEntity,
    device_time_to_aware,
    setup_device_entities,
    to_local,
)
from .hub import CulHub

PARALLEL_UPDATES = 0

OFF_TEMPERATURE = 4.5
#: Used when switching from OFF to HEAT before any set point is known.
DEFAULT_ON_TEMPERATURE = 21.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CulConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a climate entity per thermostat and one per heating zone (MAX! group)."""
    hub = entry.runtime_data
    setup_device_entities(
        hass,
        hub,
        async_add_entities,
        lambda device: [MaxClimate(hub, device)] if device.device_type.is_thermostat else [],
    )

    added: set[int] = set()

    @callback
    def add_zones(*_args: Any) -> None:
        new = hub.zones() - added
        added.update(new)
        async_add_entities(MaxZoneClimate(hub, group) for group in sorted(new))

    add_zones()
    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_zones_changed(entry.entry_id), add_zones)
    )


class MaxClimate(CulDeviceEntity, ClimateEntity):
    """A MAX! radiator or wall thermostat."""

    _attr_name = None
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_hvac_modes = [HVACMode.AUTO, HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = [PRESET_NONE, PRESET_BOOST]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_HALVES
    _attr_target_temperature_step = 0.5
    _attr_min_temp = 5.0
    _attr_max_temp = 30.5

    def __init__(self, hub: Any, device: MaxDevice) -> None:
        super().__init__(hub, device, "climate")
        self._last_on_temperature: float | None = None
        self._mode_before_boost: Mode | None = None

    # ------------------------------------------------------------------ state

    @callback
    def _device_updated(self, device: MaxDevice) -> None:
        state = device.state
        if state.target_temperature and state.target_temperature > OFF_TEMPERATURE:
            self._last_on_temperature = state.target_temperature
        if state.mode is not None and state.mode is not Mode.BOOST:
            self._mode_before_boost = state.mode
        super()._device_updated(device)

    @property
    def hvac_mode(self) -> HVACMode | None:
        state = self.device.state
        if state.mode is None:
            return None
        if state.mode is Mode.AUTO:
            return HVACMode.AUTO
        if state.target_temperature is not None and state.target_temperature <= OFF_TEMPERATURE:
            return HVACMode.OFF
        return HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction | None:
        state = self.device.state
        if state.target_temperature is not None and state.target_temperature <= OFF_TEMPERATURE:
            return HVACAction.OFF
        if state.valve_position is None:
            return None  # wall thermostats have no valve
        return HVACAction.HEATING if state.valve_position > 0 else HVACAction.IDLE

    @property
    def preset_mode(self) -> str | None:
        if self.device.state.mode is None:
            return None
        return PRESET_BOOST if self.device.state.mode is Mode.BOOST else PRESET_NONE

    @property
    def target_temperature(self) -> float | None:
        return self.device.state.target_temperature

    @property
    def current_temperature(self) -> float | None:
        return self.device.state.current_temperature

    # --------------------------------------------------------------- commands

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature; stays in AUTO until the next switch point."""
        temperature = kwargs[ATTR_TEMPERATURE]
        hvac_mode = kwargs.get(ATTR_HVAC_MODE)
        if hvac_mode is HVACMode.OFF:
            await self.async_set_hvac_mode(HVACMode.OFF)
            return
        auto = hvac_mode is HVACMode.AUTO or (hvac_mode is None and self.hvac_mode is HVACMode.AUTO)
        await self._command(
            self.hub.protocol.set_temperature(
                self.device.address, temperature, Mode.AUTO if auto else Mode.MANUAL
            )
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Switch between week profile, manual and off."""
        protocol = self.hub.protocol
        address = self.device.address
        if hvac_mode is HVACMode.AUTO:
            await self._command(protocol.set_auto(address))
        elif hvac_mode is HVACMode.OFF:
            await self._command(protocol.set_temperature(address, OFF_TEMPERATURE))
        else:
            temperature = self._last_on_temperature or DEFAULT_ON_TEMPERATURE
            await self._command(protocol.set_temperature(address, temperature))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Start boost, or end it by returning to the mode before."""
        if preset_mode == PRESET_BOOST:
            await self._command(self.hub.protocol.set_boost(self.device.address))
        elif self._mode_before_boost is Mode.MANUAL:
            await self.async_set_hvac_mode(HVACMode.HEAT)
        else:
            await self.async_set_hvac_mode(HVACMode.AUTO)

    # ---------------------------------------------------------- entity services

    async def async_set_temporary_temperature(self, temperature: float, until: datetime) -> None:
        """Hold a temperature until a point in time, then return to the week profile."""
        local = to_local(until)
        if local <= dt_util.now():
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="until_in_past")
        await self._command(
            self.hub.protocol.set_temperature(
                self.device.address, temperature, until=local.replace(tzinfo=None)
            )
        )

    async def async_set_week_profile(
        self, profile: dict[str, list[dict[str, Any]]], base_temperature: float | None = None
    ) -> None:
        """Write a week profile given as periods per weekday.

        Days not given get the base temperature all day.
        """
        week = self._parse_week_profile(profile, base_temperature)
        await self._command(self.hub.protocol.set_week_profile(self.device.address, week))

    @staticmethod
    def _parse_week_profile(
        profile: dict[str, list[dict[str, Any]]], base_temperature: float | None
    ) -> WeekProfile:
        base = base_temperature if base_temperature is not None else DEFAULT_BASE_TEMPERATURE
        try:
            week = WeekProfile.from_days(
                {
                    name: DayProfile.from_periods(
                        [
                            (p["from"], p["to"], float(p["temperature"]))
                            for p in profile.get(name, [])
                        ],
                        base,
                    )
                    for name in WEEKDAY_NAMES
                }
            )
        except ValueError as err:  # PayloadError is a ValueError
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_week_profile",
                translation_placeholders={"error": str(err)},
            ) from err
        return week

    async def async_get_week_profile(self) -> ServiceResponse:
        """Return the week profile last written to the device (None if unknown)."""
        profile = self.device.week_profile
        if profile is None:
            return {"known": False, "profile": None, "next_change": None}
        now = dt_util.now().replace(tzinfo=None)
        next_switch = profile.next_switch(now)
        return {
            "known": True,
            "profile": profile.to_dict(),
            "scheduled_temperature": profile.target_at(now),
            "next_change": {
                "at": device_time_to_aware(next_switch[0]).isoformat(),
                "temperature": next_switch[1],
            }
            if next_switch
            else None,
        }

    async def async_set_group(self, group: int) -> None:
        """Assign the thermostat to a group (room); 0 removes it from its group."""
        await self._command(self.hub.protocol.set_group(self.device.address, group))


class MaxZoneClimate(MaxClimate):
    """All thermostats of one MAX! group as one heating zone.

    State comes from the zone's lead device (its wall thermostat, otherwise the
    thermostat received best). Commands go to the lead device with the group
    id, so every thermostat of the zone applies them with one radio message.
    """

    _attr_translation_key = "zone"

    def __init__(self, hub: CulHub, group: int) -> None:
        # Not MaxClimate.__init__: the zone has no fixed device.
        CulEntity.__init__(self, hub)
        self.group = group
        self._last_on_temperature = None
        self._mode_before_boost = None
        self._attr_unique_id = f"{hub.entry.entry_id}_zone_{group}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{hub.entry.entry_id}_zone_{group}")},
            manufacturer="eQ-3",
            model="MAX! heating zone",
            translation_key="zone",
            translation_placeholders={"group": str(group)},
            via_device=(DOMAIN, hub.entry.entry_id),
        )

    @property
    def device(self) -> MaxDevice:  # type: ignore[override]
        """The lead device; only called while available."""
        lead = self.hub.zone_lead(self.group)
        assert lead is not None
        return lead

    @property
    def available(self) -> bool:
        return super().available and self.hub.zone_lead(self.group) is not None

    async def async_added_to_hass(self) -> None:
        """Follow all devices: any of them may join or leave the zone."""
        await CulEntity.async_added_to_hass(self)
        for device in self.hub.devices.values():
            self.async_on_remove(device.add_listener(self._device_updated))
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_device_added(self.hub.entry.entry_id),
                lambda device: self.async_on_remove(device.add_listener(self._device_updated)),
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_zones_changed(self.hub.entry.entry_id), self.async_write_ha_state
            )
        )

    @callback
    def _device_updated(self, device: MaxDevice) -> None:
        if device.group != self.group:
            return
        if device is self.hub.zone_lead(self.group):
            super()._device_updated(device)  # remembers the last on temperature
        else:
            self.async_write_ha_state()

    @property
    def hvac_action(self) -> HVACAction | None:
        """Heating while any radiator thermostat of the zone opens its valve."""
        if self.hvac_mode is HVACMode.OFF:
            return HVACAction.OFF
        valves = [
            d.state.valve_position
            for d in self.hub.protocol.devices_in_group(self.group)
            if d.state.valve_position is not None
        ]
        if not valves:
            return None
        return HVACAction.HEATING if any(valves) else HVACAction.IDLE

    async def async_set_week_profile(
        self, profile: dict[str, list[dict[str, Any]]], base_temperature: float | None = None
    ) -> None:
        """Write the week profile to every thermostat of the zone.

        Profiles are stored per device: the group id does not apply to them.
        """
        week = self._parse_week_profile(profile, base_temperature)
        for device in self._thermostats():
            await self._command(self.hub.protocol.set_week_profile(device.address, week))

    async def async_set_group(self, group: int) -> None:
        """Not available for a zone: change the group of single thermostats."""
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_for_zone")

    def _thermostats(self) -> list[MaxDevice]:
        return [
            d for d in self.hub.protocol.devices_in_group(self.group) if d.device_type.is_thermostat
        ]
