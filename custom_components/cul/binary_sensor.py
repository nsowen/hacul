# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Binary sensors of MAX! devices and of the CUL sticks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pycul import Cul
from pycul.protocols.max import DeviceType, MaxDevice, ShutterState

from . import CulConfigEntry
from .entity import CulDeviceEntity, CulHubEntity, setup_device_entities
from .hub import CulHub

PARALLEL_UPDATES = 0

_BATTERY_DEVICES = (
    DeviceType.HEATING_THERMOSTAT,
    DeviceType.HEATING_THERMOSTAT_PLUS,
    DeviceType.WALL_THERMOSTAT,
    DeviceType.SHUTTER_CONTACT,
    DeviceType.PUSH_BUTTON,
)


@dataclass(frozen=True, kw_only=True)
class MaxBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a MAX! device binary sensor."""

    value_fn: Callable[[MaxDevice], bool | None]
    exists_fn: Callable[[MaxDevice], bool]


DEVICE_BINARY_SENSORS: tuple[MaxBinarySensorDescription, ...] = (
    MaxBinarySensorDescription(
        key="window",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda d: None if d.state.window is None else d.state.window is ShutterState.OPEN,
        exists_fn=lambda d: d.device_type is DeviceType.SHUTTER_CONTACT,
    ),
    MaxBinarySensorDescription(
        key="battery",
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.state.battery_low,
        exists_fn=lambda d: d.device_type in _BATTERY_DEVICES,
    ),
    MaxBinarySensorDescription(
        key="panel_locked",
        translation_key="panel_locked",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.state.panel_locked,
        exists_fn=lambda d: d.device_type.is_thermostat,
    ),
    MaxBinarySensorDescription(
        key="dst_active",
        translation_key="dst_active",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.state.dst_active,
        exists_fn=lambda d: d.device_type.is_thermostat,
    ),
    MaxBinarySensorDescription(
        key="gateway_known",
        translation_key="gateway_known",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.state.gateway_known,
        exists_fn=lambda d: d.device_type.is_thermostat,
    ),
    MaxBinarySensorDescription(
        key="radio_error",
        translation_key="radio_error",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.state.rf_error,
        exists_fn=lambda d: True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CulConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add device binary sensors and a connectivity sensor per stick."""
    hub = entry.runtime_data
    async_add_entities([CulConnectedSensor(hub, cul) for cul in hub.culs])
    setup_device_entities(
        hass,
        hub,
        async_add_entities,
        lambda device: [
            MaxBinarySensor(hub, device, description)
            for description in DEVICE_BINARY_SENSORS
            if description.exists_fn(device)
        ],
    )


class MaxBinarySensor(CulDeviceEntity, BinarySensorEntity):
    """A flag reported by a MAX! device."""

    entity_description: MaxBinarySensorDescription

    def __init__(
        self, hub: CulHub, device: MaxDevice, description: MaxBinarySensorDescription
    ) -> None:
        super().__init__(hub, device, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.device)

    def _state_key(self) -> object:
        return self.is_on


class CulConnectedSensor(CulHubEntity, BinarySensorEntity):
    """Whether a stick is connected."""

    _attr_translation_key = "stick_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: CulHub, cul: Cul) -> None:
        super().__init__(hub, f"connected_{cul.name}")
        self.cul = cul
        self._attr_translation_placeholders = {"stick": cul.name}

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.cul.connected
