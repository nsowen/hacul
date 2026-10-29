# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The CUL integration: eQ-3 MAX! devices through busware CUL sticks."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .external import ExternalSensorBridge
from .hub import CulHub
from .schedule_sync import ScheduleSync
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.EVENT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type CulConfigEntry = ConfigEntry[CulHub]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services (independent of config entries)."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CulConfigEntry) -> bool:
    """Open the sticks and set up the platforms."""
    hub = CulHub(hass, entry)
    await hub.async_setup()
    entry.runtime_data = hub

    hub.device_id = (
        dr.async_get(hass)
        .async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="busware",
            model="CUL",
            name=entry.title,
            sw_version=", ".join(c.version.version for c in hub.culs if c.version),
        )
        .id
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    schedules = ScheduleSync(hass, hub)
    external = ExternalSensorBridge(hass, hub)
    entry.async_on_unload(schedules.async_start())
    entry.async_on_unload(external.async_start())
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CulConfigEntry) -> bool:
    """Unload platforms and close the sticks."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_unload()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: CulConfigEntry) -> None:
    """Reload after the options changed (sticks, schedules, external sensors)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: CulConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow removing a MAX! device; the hub device itself stays."""
    for domain, identifier in device.identifiers:
        if domain != DOMAIN:
            continue
        if identifier == entry.entry_id:
            return False
        if identifier.startswith(f"{entry.entry_id}_zone_"):
            # A heating zone can go once it has no thermostats any more.
            group = int(identifier.rsplit("_", 1)[1])
            return group not in entry.runtime_data.zones()
        entry.runtime_data.remove_device(identifier)
    return True
