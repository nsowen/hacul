# SPDX-License-Identifier: GPL-2.0-or-later
"""Numbers for the write-only temperature and valve configuration.

MAX! devices never report their configuration over the air: what was written
last ( or the factory default) is all the integration knows. Writing is a
whole-block operation, so changing one number keeps the others as last
written (see CulHub.async_configure_temperatures).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pycul.protocols.max import DeviceType, MaxDevice
from pycul.protocols.max.const import MAX_TEMPERATURE, MIN_TEMPERATURE

from . import CulConfigEntry
from .entity import CulDeviceEntity, setup_device_entities
from .hub import CulHub

PARALLEL_UPDATES = 0

RADIATOR_THERMOSTATS = (DeviceType.HEATING_THERMOSTAT, DeviceType.HEATING_THERMOSTAT_PLUS)


@dataclass(frozen=True, slots=True)
class NumberSpec:
    """One configurable value: config field and its value range."""

    key: str  # field of TemperatureConfig/ValveConfig and entity translation key
    minimum: float
    maximum: float
    step: float
    unit: str | None = None
    device_class: NumberDeviceClass | None = None
    integer: bool = False  # the config field is an int


TEMPERATURE_NUMBERS = (
    NumberSpec(
        "comfort",
        MIN_TEMPERATURE,
        MAX_TEMPERATURE,
        0.5,
        UnitOfTemperature.CELSIUS,
        NumberDeviceClass.TEMPERATURE,
    ),
    NumberSpec(
        "eco",
        MIN_TEMPERATURE,
        MAX_TEMPERATURE,
        0.5,
        UnitOfTemperature.CELSIUS,
        NumberDeviceClass.TEMPERATURE,
    ),
    NumberSpec(
        "maximum",
        MIN_TEMPERATURE,
        MAX_TEMPERATURE,
        0.5,
        UnitOfTemperature.CELSIUS,
        NumberDeviceClass.TEMPERATURE,
    ),
    NumberSpec(
        "minimum",
        MIN_TEMPERATURE,
        MAX_TEMPERATURE,
        0.5,
        UnitOfTemperature.CELSIUS,
        NumberDeviceClass.TEMPERATURE,
    ),
    NumberSpec(
        "measurement_offset",
        -3.5,
        3.5,
        0.5,
        UnitOfTemperature.CELSIUS,
        NumberDeviceClass.TEMPERATURE,
    ),
    NumberSpec(
        "window_open_temperature",
        MIN_TEMPERATURE,
        MAX_TEMPERATURE,
        0.5,
        UnitOfTemperature.CELSIUS,
        NumberDeviceClass.TEMPERATURE,
    ),
    NumberSpec(
        "window_open_duration",
        0,
        60,
        5,
        UnitOfTime.MINUTES,
        NumberDeviceClass.DURATION,
        integer=True,
    ),
)

VALVE_NUMBERS = (
    NumberSpec("boost_valve_position", 0, 100, 5, PERCENTAGE, integer=True),
    NumberSpec(
        "decalcification_hour", 0, 23, 1, UnitOfTime.HOURS, NumberDeviceClass.DURATION, integer=True
    ),
    NumberSpec("max_valve_setting", 0, 100, 1, PERCENTAGE, integer=True),
    NumberSpec("valve_offset", 0, 100, 1, PERCENTAGE, integer=True),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CulConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the configuration numbers of the thermostats."""
    hub = entry.runtime_data
    setup_device_entities(
        hass,
        hub,
        async_add_entities,
        lambda device: _numbers(hub, device),
    )


def _numbers(hub: CulHub, device: MaxDevice) -> list[NumberEntity]:
    entities: list[NumberEntity] = []
    if device.device_type.is_thermostat:
        entities.extend(MaxTemperatureNumber(hub, device, spec) for spec in TEMPERATURE_NUMBERS)
    if device.device_type in RADIATOR_THERMOSTATS:
        entities.extend(MaxValveNumber(hub, device, spec) for spec in VALVE_NUMBERS)
    return entities


class MaxConfigNumber(CulDeviceEntity, NumberEntity):
    """One value of the write-only configuration of a thermostat."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, hub: CulHub, device: MaxDevice, spec: NumberSpec) -> None:
        super().__init__(hub, device, spec.key)
        self._spec = spec
        self._attr_translation_key = spec.key
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_device_class = spec.device_class
        self._attr_native_min_value = spec.minimum
        self._attr_native_max_value = spec.maximum
        self._attr_native_step = spec.step

    @property
    def native_value(self) -> float | None:
        return getattr(self._config(), self._spec.key)

    def _state_key(self) -> object:
        return self.native_value

    async def async_set_native_value(self, value: float) -> None:
        """Write one setting; the radio message carries the whole block."""
        typed: Any = int(value) if self._spec.integer else value
        await self._command(self._write(**{self._spec.key: typed}))
        self.async_write_ha_state()

    def _config(self) -> Any:
        raise NotImplementedError

    def _write(self, **changes: Any) -> Any:
        raise NotImplementedError


class MaxTemperatureNumber(MaxConfigNumber):
    """Comfort/eco/min/max/offset/window-open settings of a thermostat."""

    def _config(self) -> Any:
        return self.hub.temperature_config(self.device.address)

    def _write(self, **changes: Any) -> Any:
        return self.hub.async_configure_temperatures(self.device.address, **changes)


class MaxValveNumber(MaxConfigNumber):
    """Valve settings of a radiator thermostat."""

    def _config(self) -> Any:
        return self.hub.valve_config(self.device.address)

    def _write(self, **changes: Any) -> Any:
        return self.hub.async_configure_valve(self.device.address, **changes)
