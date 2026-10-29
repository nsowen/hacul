# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Button to start pairing new MAX! devices."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CulConfigEntry
from .const import PAIRING_DURATION
from .entity import CulHubEntity
from .hub import CulHub

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CulConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the pairing button."""
    async_add_entities([CulPairingButton(entry.runtime_data)])


class CulPairingButton(CulHubEntity, ButtonEntity):
    """Accept pairing requests for 60 seconds.

    Press the pair (boost) button of the device afterwards; new devices
    appear automatically.
    """

    _attr_translation_key = "start_pairing"

    def __init__(self, hub: CulHub) -> None:
        super().__init__(hub, "start_pairing")

    async def async_press(self) -> None:
        """Start the pairing window."""
        self.hub.protocol.start_pairing(PAIRING_DURATION)
