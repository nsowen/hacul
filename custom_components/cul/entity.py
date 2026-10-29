# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Base entities for the CUL integration."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
import homeassistant.util.dt as dt_util
from pycul import CulError
from pycul.protocols.max import DeviceType, MaxDevice

from .const import DOMAIN, signal_connection, signal_device_added, signal_hub_updated
from .hub import CulHub


def to_local(value: datetime) -> datetime:
    """Return `value` in HA's time zone; naive values are taken as local time."""
    if value.tzinfo is None:
        return value.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_local(value)


def device_time_to_aware(value: datetime) -> datetime:
    """MAX! devices work in local time without zone: attach HA's time zone."""
    return value.replace(tzinfo=dt_util.get_default_time_zone())


MODELS: dict[DeviceType, str] = {
    DeviceType.HEATING_THERMOSTAT: "MAX! Radiator Thermostat",
    DeviceType.HEATING_THERMOSTAT_PLUS: "MAX! Radiator Thermostat+",
    DeviceType.WALL_THERMOSTAT: "MAX! Wall Thermostat+",
    DeviceType.SHUTTER_CONTACT: "MAX! Window Contact",
    DeviceType.PUSH_BUTTON: "MAX! Eco Switch",
    DeviceType.PLUG_ADAPTER: "MAX! Plug Adapter",
}


class CulEntity(Entity):
    """Common behaviour: push updates, availability follows the sticks."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: CulHub) -> None:
        self.hub = hub

    @property
    def available(self) -> bool:
        """Return True while at least one stick is connected."""
        return self.hub.available

    async def async_added_to_hass(self) -> None:
        """Follow the availability of the sticks."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_connection(self.hub.entry.entry_id), self.async_write_ha_state
            )
        )

    async def _command(self, action: Coroutine[Any, Any, Any]) -> Any:
        """Run a radio command and turn library errors into HA errors."""
        try:
            return await action
        except CulError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err


class CulDeviceEntity(CulEntity):
    """An entity of a MAX! device."""

    def __init__(self, hub: CulHub, device: MaxDevice, key: str) -> None:
        super().__init__(hub)
        self.device = device
        self._attr_unique_id = f"{device.address}_{key}"
        model = MODELS.get(device.device_type, "MAX! device")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.address)},
            manufacturer="eQ-3",
            model=model,
            model_id=device.device_type.name.lower(),
            name=f"{model} {device.address}",
            serial_number=device.serial,
            sw_version=device.firmware,
            via_device=(DOMAIN, hub.entry.entry_id),
        )

    async def async_added_to_hass(self) -> None:
        """Follow the device's state updates."""
        await super().async_added_to_hass()
        self.async_on_remove(self.device.add_listener(self._device_updated))

    @callback
    def _device_updated(self, device: MaxDevice) -> None:
        # Every frame of a device notifies all its entities; only write what changed.
        state = self._state_key()
        if state is None or state != self._last_state:
            self._last_state = state
            self.async_write_ha_state()

    def _state_key(self) -> object:
        """Return what the entity shows; None writes on every update."""
        return None

    _last_state: object = None


class CulHubEntity(CulEntity):
    """An entity of the hub (the sticks and the send queue)."""

    def __init__(self, hub: CulHub, key: str) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, hub.entry.entry_id)})

    async def async_added_to_hass(self) -> None:
        """Also follow credit and queue changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_hub_updated(self.hub.entry.entry_id), self.async_write_ha_state
            )
        )


def setup_device_entities(
    hass: HomeAssistant,
    hub: CulHub,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[MaxDevice], list[Entity]],
) -> None:
    """Add entities for known devices and for devices paired later."""
    async_add_entities([entity for device in hub.devices.values() for entity in factory(device)])

    @callback
    def device_added(device: MaxDevice) -> None:
        async_add_entities(factory(device))

    hub.entry.async_on_unload(
        async_dispatcher_connect(hass, signal_device_added(hub.entry.entry_id), device_added)
    )
