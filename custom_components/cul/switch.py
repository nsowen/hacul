# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Settings of MAX! devices that can be switched."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pycul.protocols.max import DeviceType, MaxDevice

from . import CulConfigEntry
from .entity import CulDeviceEntity, setup_device_entities
from .hub import CulHub

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CulConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the display setting of wall thermostats."""
    hub = entry.runtime_data
    setup_device_entities(
        hass,
        hub,
        async_add_entities,
        lambda device: (
            [MaxDisplayActualTemperature(hub, device)]
            if device.device_type is DeviceType.WALL_THERMOSTAT
            else []
        ),
    )


class MaxDisplayActualTemperature(CulDeviceEntity, SwitchEntity):
    """Wall thermostat shows the measured (on) or the target temperature (off)."""

    _attr_translation_key = "display_actual_temperature"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hub: CulHub, device: MaxDevice) -> None:
        super().__init__(hub, device, "display_actual_temperature")

    @property
    def is_on(self) -> bool | None:
        return self.device.state.display_actual_temperature

    def _state_key(self) -> object:
        return self.is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Show the measured temperature."""
        await self._command(
            self.hub.protocol.set_display_actual_temperature(self.device.address, True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Show the target temperature."""
        await self._command(
            self.hub.protocol.set_display_actual_temperature(self.device.address, False)
        )
