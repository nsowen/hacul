# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Keep thermostat week profiles in sync with Home Assistant schedule helpers.

The thermostat runs its week profile on its own, also when Home Assistant is
offline. A schedule helper is only the editor: its blocks become heating
periods (temperature from the block's `temperature` data field, or the
configured default), the time between blocks gets the base temperature.

Schedule edits do not always change the helper's state, so besides reacting to
state changes the helpers are compared periodically. Only days that differ
from the profile known to be on the device are sent.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from functools import partial
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.schedule.const import DOMAIN as SCHEDULE_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from pycul import CulError
from pycul.protocols.max import DayProfile, PayloadError, WeekProfile
from pycul.protocols.max.weekprofile import WEEKDAY_NAMES

from .const import (
    CONF_BASE_TEMPERATURE,
    CONF_DEFAULT_TEMPERATURE,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULES,
    DEFAULT_BASE_TEMPERATURE,
    DEFAULT_SCHEDULE_TEMPERATURE,
    DOMAIN,
    SCHEDULE_POLL_INTERVAL,
)

if TYPE_CHECKING:
    from .hub import CulHub

_LOGGER = logging.getLogger(__name__)

ATTR_TEMPERATURE = "temperature"
SYNC_COOLDOWN = 5.0


def schedule_to_week_profile(
    schedule: dict[str, list[dict[str, Any]]],
    base_temperature: float,
    default_temperature: float,
) -> WeekProfile:
    """Convert a schedule helper configuration into a week profile.

    Raises PayloadError if a day cannot be represented (e.g. more than 13
    switch points, or a temperature outside 4.5..30.5 in 0.5 steps).
    """
    days: dict[str, DayProfile] = {}
    for name in WEEKDAY_NAMES:
        periods = [
            (
                block["from"],
                block["to"],
                float((block.get("data") or {}).get(ATTR_TEMPERATURE, default_temperature)),
            )
            for block in schedule.get(name, [])
        ]
        try:
            days[name] = DayProfile.from_periods(periods, base_temperature)
        except PayloadError as err:
            raise PayloadError(f"{name}: {err}") from err
    return WeekProfile.from_days(days)


class ScheduleSync:
    """Pushes schedule helper changes to the linked thermostats."""

    def __init__(self, hass: HomeAssistant, hub: CulHub) -> None:
        self.hass = hass
        self.hub = hub
        self._config: dict[str, dict[str, Any]] = hub.entry.options.get(CONF_SCHEDULES, {})
        self._debouncers: dict[str, Debouncer[Any]] = {}

    @callback
    def async_start(self) -> Callable[[], None]:
        """Start listening; returns a function that stops it."""
        if not self._config:
            return lambda: None
        unsubscribe: list[Callable[[], None]] = []
        for address in self._config:
            self._debouncers[address] = Debouncer(
                self.hass,
                _LOGGER,
                cooldown=SYNC_COOLDOWN,
                immediate=False,
                # partial (not lambda): HassJob must see the coroutine function
                function=partial(self.async_sync, address),
            )
        schedule_entities = {c[CONF_SCHEDULE_ENTITY] for c in self._config.values()}
        unsubscribe.append(
            async_track_state_change_event(
                self.hass, list(schedule_entities), self._on_schedule_changed
            )
        )
        unsubscribe.append(
            async_track_time_interval(self.hass, self._sync_all, SCHEDULE_POLL_INTERVAL)
        )
        for debouncer in self._debouncers.values():
            self.hass.async_create_task(debouncer.async_call())

        def stop() -> None:
            for remove in unsubscribe:
                remove()
            for debouncer in self._debouncers.values():
                debouncer.async_shutdown()

        return stop

    @callback
    def _on_schedule_changed(self, event: Event[EventStateChangedData]) -> None:
        for address, config in self._config.items():
            if config[CONF_SCHEDULE_ENTITY] == event.data["entity_id"]:
                self.hass.async_create_task(self._debouncers[address].async_call())

    async def _sync_all(self, _now: datetime) -> None:
        for debouncer in self._debouncers.values():
            await debouncer.async_call()

    async def async_sync(self, address: str) -> None:
        """Read the schedule helper and write differing days to the thermostat."""
        config = self._config[address]
        entity_id = config[CONF_SCHEDULE_ENTITY]
        if address not in self.hub.devices or not self.hub.available:
            return
        try:
            response = await self.hass.services.async_call(
                SCHEDULE_DOMAIN,
                "get_schedule",
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Cannot read schedule %s: %s", entity_id, err)
            return
        schedule: dict[str, list[dict[str, Any]]] = (response or {}).get(entity_id, {})  # type: ignore[assignment]

        issue_id = f"schedule_invalid_{address}"
        try:
            profile = schedule_to_week_profile(
                schedule,
                config.get(CONF_BASE_TEMPERATURE, DEFAULT_BASE_TEMPERATURE),
                config.get(CONF_DEFAULT_TEMPERATURE, DEFAULT_SCHEDULE_TEMPERATURE),
            )
        except PayloadError as err:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="schedule_invalid",
                translation_placeholders={
                    "schedule": entity_id,
                    "device": address,
                    "error": str(err),
                },
            )
            return
        ir.async_delete_issue(self.hass, DOMAIN, issue_id)

        device = self.hub.devices[address]
        if device.week_profile == profile:
            return
        _LOGGER.info(
            "Writing week profile from %s to %s (days: %s)",
            entity_id,
            address,
            [WEEKDAY_NAMES[d] for d in profile.changed_days(device.week_profile)],
        )
        try:
            await self.hub.protocol.set_week_profile(address, profile)
        except CulError as err:
            # Days that were acknowledged are kept; the rest is retried on the next poll.
            _LOGGER.warning("Week profile for %s not completely written: %s", address, err)
