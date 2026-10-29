# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Key presses of MAX! eco switches."""

from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pycul.protocols.max import ButtonState, DeviceType, MaxDevice

from . import CulConfigEntry
from .entity import CulDeviceEntity, setup_device_entities
from .hub import CulHub

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CulConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add an event entity per eco switch."""
    hub = entry.runtime_data
    setup_device_entities(
        hass,
        hub,
        async_add_entities,
        lambda device: (
            [MaxButtonEvent(hub, device)] if device.device_type is DeviceType.PUSH_BUTTON else []
        ),
    )


class MaxButtonEvent(CulDeviceEntity, EventEntity):
    """Fires "auto" or "eco" when a key of the eco switch is pressed.

    The switch sends a press to every linked thermostat; pycul counts it once.
    """

    _attr_translation_key = "button"
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = [state.value for state in ButtonState]

    def __init__(self, hub: CulHub, device: MaxDevice) -> None:
        super().__init__(hub, device, "button")
        self._presses = 0

    async def async_added_to_hass(self) -> None:
        """Count presses from now on."""
        await super().async_added_to_hass()
        self._presses = self.device.state.button_presses

    @callback
    def _device_updated(self, device: MaxDevice) -> None:
        if device.state.button_presses == self._presses or device.state.button is None:
            return
        self._presses = device.state.button_presses
        self._trigger_event(device.state.button.value)
        self.async_write_ha_state()
