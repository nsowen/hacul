# SPDX-License-Identifier: GPL-2.0-or-later
"""Selects for the write-only valve configuration of radiator thermostats.

Settings with a fixed set of choices (boost duration, decalcification day);
the numeric settings are in number.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pycul.protocols.max import DeviceType, MaxDevice
from pycul.protocols.max.const import BOOST_DURATIONS, WEEKDAYS

from . import CulConfigEntry
from .entity import CulDeviceEntity, setup_device_entities
from .hub import CulHub

PARALLEL_UPDATES = 0

RADIATOR_THERMOSTATS = (DeviceType.HEATING_THERMOSTAT, DeviceType.HEATING_THERMOSTAT_PLUS)

#: Boost durations in minutes, from the choices the protocol allows.
BOOST_MINUTES = tuple(str(minutes) for minutes in sorted(BOOST_DURATIONS.values()))

#: Weekdays in the order they are shown; the protocol stores its own order.
DECALC_DAYS = tuple(
    day for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun") if day in WEEKDAYS
)


@dataclass(frozen=True, slots=True)
class SelectSpec:
    """One configurable choice: config field and its options."""

    key: str  # field of ValveConfig and entity translation key
    options: tuple[str, ...]
    integer: bool = False  # the config field is an int


VALVE_SELECTS = (
    SelectSpec("boost_duration", BOOST_MINUTES, integer=True),
    SelectSpec("decalcification_day", DECALC_DAYS),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CulConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the configuration selects of the radiator thermostats."""
    hub = entry.runtime_data
    setup_device_entities(
        hass,
        hub,
        async_add_entities,
        lambda device: (
            [MaxValveSelect(hub, device, spec) for spec in VALVE_SELECTS]
            if device.device_type in RADIATOR_THERMOSTATS
            else []
        ),
    )


class MaxValveSelect(CulDeviceEntity, SelectEntity):
    """One choice of the write-only valve configuration."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hub: CulHub, device: MaxDevice, spec: SelectSpec) -> None:
        super().__init__(hub, device, spec.key)
        self._spec = spec
        self._attr_translation_key = spec.key

    @property
    def options(self) -> list[str]:
        return list(self._spec.options)

    @property
    def current_option(self) -> str | None:
        return str(getattr(self.hub.valve_config(self.device.address), self._spec.key))

    def _state_key(self) -> object:
        return self.current_option

    async def async_select_option(self, option: str) -> None:
        """Write one setting; the radio message carries the whole block."""
        value: Any = int(option) if self._spec.integer else option
        await self._command(
            self.hub.async_configure_valve(self.device.address, **{self._spec.key: value})
        )
        self.async_write_ha_state()
