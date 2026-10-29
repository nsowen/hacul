# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Diagnostics for the CUL integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import CulConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CulConfigEntry
) -> dict[str, Any]:
    """Return sticks, queue and devices (no secrets are involved)."""
    hub = entry.runtime_data
    return {
        "data": dict(entry.data),
        "options": dict(entry.options),
        "sticks": [
            {
                "name": cul.name,
                "connected": cul.connected,
                "firmware": cul.version.raw if cul.version else None,
                "credit10ms": cul.credits,
            }
            for cul in hub.culs
        ],
        "queue": [
            {
                "message": str(message),
                "attempts": message.attempts,
                "held": message.hold_until_released and not message.released,
            }
            for message in hub.protocol.queue.pending
        ],
        "waiting_for_credit": hub.protocol.queue.waiting_for_credit,
        "links": hub.links,
        "devices": [
            {**device.to_dict(), "state": asdict(device.state), "rssi_by_cul": device.rssi_by_cul}
            for device in hub.devices.values()
        ],
    }
