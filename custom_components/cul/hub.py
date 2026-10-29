# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The CUL hub: sticks, the MAX! network and persisted device data."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components import usb
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from pycul import Cul, CulError, SendCancelledError
from pycul.protocols.max import DeviceType, MaxDevice, MaxProtocol

from .const import (
    CONF_ADOPT_DEVICES,
    CONF_BAUDRATE,
    CONF_EXTERNAL,
    CONF_FAKE_SC_ADDRESS,
    CONF_FAKE_WT_ADDRESS,
    CONF_FAST_SEND,
    CONF_MAX_ADDRESS,
    CONF_NAME,
    CONF_STICKS,
    CONF_TEMPERATURE_SENSOR,
    CONF_URL,
    CONF_WINDOW_SENSOR,
    CREDIT_REFRESH_INTERVAL,
    DEFAULT_BAUDRATE,
    DOMAIN,
    STORAGE_SAVE_DELAY,
    STORAGE_VERSION,
    signal_connection,
    signal_device_added,
    signal_hub_updated,
    signal_zones_changed,
)

if TYPE_CHECKING:
    from . import CulConfigEntry

_LOGGER = logging.getLogger(__name__)

#: Link kinds stored per thermostat: fake device -> option that requires it.
_LINKS = {"fake_wt": CONF_TEMPERATURE_SENSOR, "fake_sc": CONF_WINDOW_SENSOR}


class CulHub:
    """Connects the configured sticks to one MAX! network."""

    def __init__(self, hass: HomeAssistant, entry: CulConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.culs: list[Cul] = []
        self.protocol: MaxProtocol
        #: Fake device links confirmed by the thermostats: {address: {"fake_wt": True}}.
        self.links: dict[str, dict[str, bool]] = {}
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._unsubscribe: list[Callable[[], None]] = []
        #: Registry id of the hub device (the CUL), parent of all MAX! devices.
        self.device_id: str | None = None
        #: Persisted form of each device, to save only when it changed.
        self._stored: dict[str, dict[str, Any]] = {}

    # --------------------------------------------------------------- lifecycle

    async def async_setup(self) -> None:
        """Restore devices, open the sticks and start background work."""
        data = await self._store.async_load() or {}
        devices = [MaxDevice.from_dict(d) for d in data.get("devices", [])]
        self._stored = {d.address: d.to_dict() for d in devices}
        self.links = data.get("links", {})

        config = self.entry.data
        options = self.entry.options
        self.protocol = MaxProtocol(
            config[CONF_MAX_ADDRESS],
            devices,
            adopt_devices=options.get(CONF_ADOPT_DEVICES, True),
            fake_wt_address=config.get(CONF_FAKE_WT_ADDRESS),
            fake_sc_address=config.get(CONF_FAKE_SC_ADDRESS),
            fast_send_after_wake_up=options.get(CONF_FAST_SEND, False),
        )
        for stick in config[CONF_STICKS]:
            cul = Cul(
                stick[CONF_URL],
                stick.get(CONF_BAUDRATE, DEFAULT_BAUDRATE),
                name=stick.get(CONF_NAME) or stick[CONF_URL],
            )
            cul.add_protocol(self.protocol)
            self._unsubscribe.append(cul.add_connection_listener(self._on_connection))
            self.culs.append(cul)

        for device in devices:
            self._unsubscribe.append(device.add_listener(self._on_device_update))
        self._unsubscribe.append(self.protocol.add_device_added_listener(self._on_device_added))
        self._unsubscribe.append(self.protocol.queue.add_listener(self._signal_hub))

        results = await asyncio.gather(
            *(cul.connect() for cul in self.culs), return_exceptions=True
        )
        if all(isinstance(r, Exception) for r in results):
            await self._close_sticks()
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(results[0])},
            )
        for cul, result in zip(self.culs, results, strict=True):
            if isinstance(result, Exception):
                _LOGGER.warning("Stick %s not available yet: %s", cul.name, result)
                cul.request_reconnect()  # pycul keeps trying with back-off
        self._watch_usb()

        self._unsubscribe.append(
            async_track_time_interval(self.hass, self._refresh_credits, CREDIT_REFRESH_INTERVAL)
        )
        self.entry.async_create_background_task(
            self.hass, self.async_reconcile_links(), f"{DOMAIN} reconcile links"
        )

    async def async_unload(self) -> None:
        """Stop everything and persist device data."""
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        await self._close_sticks()
        await self._store.async_save(self._data_to_store())

    async def _close_sticks(self) -> None:
        for cul in self.culs:
            await cul.close()

    @callback
    def _watch_usb(self) -> None:
        """Reconnect as soon as Home Assistant sees a USB device being plugged in.

        Without this, a re-plugged stick is found by pycul's next retry, up to a
        minute later. Any new USB device triggers a retry of disconnected
        sticks: cheap, and it also covers adapters with unknown USB ids.
        """
        if "usb" not in self.hass.config.components:
            return
        try:
            unsubscribe = usb.async_register_port_event_callback(self.hass, self._on_usb_event)
        except (AttributeError, KeyError) as err:  # older Home Assistant
            _LOGGER.debug("No USB port events: %s", err)
            return
        self._unsubscribe.append(unsubscribe)

    @callback
    def _on_usb_event(self, added: set[Any], removed: set[Any]) -> None:
        if not added:
            return
        for cul in self.culs:
            if not cul.connected and not cul.closing:
                _LOGGER.debug("USB device added, retrying stick %s", cul.name)
                cul.request_reconnect()

    # ------------------------------------------------------------------ state

    @property
    def available(self) -> bool:
        """Return True while at least one stick is connected."""
        return any(cul.connected for cul in self.culs)

    @property
    def devices(self) -> dict[str, MaxDevice]:
        """Return the known MAX! devices by address."""
        return self.protocol.devices

    def remove_device(self, address: str) -> None:
        """Forget a device (it stays paired and may be adopted again)."""
        self.protocol.remove_device(address)
        self.links.pop(address, None)
        self._stored.pop(address, None)
        self._schedule_save()

    # -------------------------------------------------------------- callbacks

    @callback
    def _on_connection(self, connected: bool) -> None:
        async_dispatcher_send(self.hass, signal_connection(self.entry.entry_id))
        self._signal_hub()

    @callback
    def _signal_hub(self) -> None:
        async_dispatcher_send(self.hass, signal_hub_updated(self.entry.entry_id))

    @callback
    def _on_device_added(self, device: MaxDevice) -> None:
        self._unsubscribe.append(device.add_listener(self._on_device_update))
        self._stored[device.address] = device.to_dict()
        if device.group:
            async_dispatcher_send(self.hass, signal_zones_changed(self.entry.entry_id))
        self._schedule_save()
        async_dispatcher_send(self.hass, signal_device_added(self.entry.entry_id), device)

    @callback
    def _on_device_update(self, device: MaxDevice) -> None:
        # Only static data (group, links, week profile, serial) is stored. Most
        # updates are state reports: re-arming the delayed save for each of them
        # would postpone it for as long as the devices keep talking.
        data = device.to_dict()
        stored = self._stored.get(device.address)
        if stored != data:
            self._stored[device.address] = data
            self._schedule_save()
            if stored is not None and stored.get("group") != data["group"]:
                async_dispatcher_send(self.hass, signal_zones_changed(self.entry.entry_id))

    async def _refresh_credits(self, _now: datetime) -> None:
        for cul in self.culs:
            if cul.connected:
                try:
                    await cul.async_get_credits()
                except CulError as err:
                    _LOGGER.debug("Credit query on %s failed: %s", cul.name, err)
        self._signal_hub()

    # ------------------------------------------------------------- persisting

    @callback
    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data_to_store, STORAGE_SAVE_DELAY)

    @callback
    def _data_to_store(self) -> dict[str, Any]:
        return {
            "devices": [device.to_dict() for device in self.protocol.devices.values()],
            "links": self.links,
        }

    def zones(self) -> set[int]:
        """Return the group ids that contain at least one thermostat."""
        return {
            d.group
            for d in self.protocol.devices.values()
            if d.group and d.device_type.is_thermostat
        }

    def zone_lead(self, group: int) -> MaxDevice | None:
        """Return the device whose state stands for a zone.

        The wall thermostat, if the zone has one: its set point and measured
        room temperature are the zone's. Otherwise the thermostat that is
        received best, which is also the one group commands are sent to.
        """
        members = [d for d in self.protocol.devices_in_group(group) if d.device_type.is_thermostat]
        for device in members:
            if device.device_type is DeviceType.WALL_THERMOSTAT:
                return device
        return self.protocol.group_representative(group) if members else None

    def device_name(self, address: str) -> str:
        """Return the name of a MAX! device as shown in Home Assistant."""
        entry = dr.async_get(self.hass).async_get_device(identifiers={(DOMAIN, address)})
        return (entry.name_by_user or entry.name or address) if entry else address

    # ------------------------------------------------------------------ zones

    async def async_group_devices(
        self, addresses: list[str], group: int, link: bool, shared: list[str] | None = None
    ) -> None:
        """Make devices one heating zone (group id and links), like a MAX! Cube does.

        This can take long: window contacts only take their frames when they
        transmit, and a zone needs many frames. Devices that could not be set
        up are reported as a repair issue.
        """
        issue_id = f"zone_incomplete_{group}"
        results = await self.protocol.group_devices(
            addresses, group, link=link, shared_window_contacts=shared or ()
        )
        failed = [
            address
            for address, error in results.items()
            if error is not None and not isinstance(error, SendCancelledError)
        ]
        for address, error in results.items():
            if error is not None:
                _LOGGER.warning("Zone %d: %s not set up: %s", group, address, error)
        if failed:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="zone_incomplete",
                translation_placeholders={
                    "group": str(group),
                    "devices": ", ".join(self.device_name(a) for a in failed),
                },
            )
        elif all(error is None for error in results.values()):
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        async_dispatcher_send(self.hass, signal_zones_changed(self.entry.entry_id))

    # ---------------------------------------------------------- fake devices

    async def async_reconcile_links(self) -> None:
        """Associate/deassociate the fake wall thermostat and window contact.

        A thermostat gets the fake wall thermostat as link partner when an HA
        temperature sensor is assigned to it, and the fake window contact when
        a window sensor is assigned. Confirmed links are stored, so this only
        sends frames when the configuration changed.
        """
        external: dict[str, dict[str, str]] = self.entry.options.get(CONF_EXTERNAL, {})
        addresses = set(external) | set(self.links)
        for address in sorted(addresses):
            if address not in self.protocol.devices:
                continue
            wanted = external.get(address, {})
            current = self.links.setdefault(address, {})
            for link, option in _LINKS.items():
                want = bool(wanted.get(option))
                if current.get(link, False) == want:
                    continue
                method = (
                    self.protocol.associate_fake_wall_thermostat
                    if link == "fake_wt"
                    else self.protocol.associate_fake_shutter_contact
                )
                try:
                    await method(address, want)
                except (CulError, ValueError) as err:
                    _LOGGER.warning(
                        "Could not %s %s for %s: %s",
                        "link" if want else "unlink",
                        link,
                        address,
                        err,
                    )
                    continue
                current[link] = want
                self._schedule_save()
