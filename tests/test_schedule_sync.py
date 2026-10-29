# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for syncing schedule helpers into week profiles."""

from __future__ import annotations

from datetime import time, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pycul.protocols.max import DayProfile, MessageType, WeekProfile
from pycul.protocols.max.payloads import PayloadError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.cul.const import (
    CONF_BASE_TEMPERATURE,
    CONF_DEFAULT_TEMPERATURE,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULES,
)
from custom_components.cul.schedule_sync import schedule_to_week_profile

from .conftest import HT, ME, FakeStick, frame


def test_schedule_to_week_profile() -> None:
    schedule = {
        "monday": [
            {"from": time(6, 0), "to": time(8, 30), "data": {"temperature": 22}},
            {"from": time(17, 0), "to": time.max},
        ],
        "saturday": [{"from": "07:30:00", "to": "23:00:00"}],
    }
    profile = schedule_to_week_profile(schedule, base_temperature=16, default_temperature=20.5)
    assert profile.days[0] == DayProfile.from_points(
        [("06:00", 16), ("08:30", 22), ("17:00", 16), ("24:00", 20.5)]
    )
    assert profile.days[1] == DayProfile.constant(16)
    assert profile.days[5] == DayProfile.from_points(
        [("07:30", 16), ("23:00", 20.5), ("24:00", 16)]
    )


def test_schedule_with_too_many_blocks() -> None:
    blocks = [{"from": time(h, 0), "to": time(h, 30)} for h in range(1, 15)]
    with pytest.raises(PayloadError, match="monday"):
        schedule_to_week_profile({"monday": blocks}, 17, 21)


async def test_schedule_is_synced(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    assert await async_setup_component(
        hass,
        "schedule",
        {
            "schedule": {
                "heating": {
                    "name": "Heating",
                    "monday": [
                        {"from": "06:00", "to": "08:30", "data": {"temperature": 22}},
                    ],
                }
            }
        },
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    stick.emit(frame(MessageType.THERMOSTAT_STATE, HT, ME, "18202800D700"))
    await hass.async_block_till_done()

    hub = config_entry.runtime_data
    hub.devices[HT].week_profile = WeekProfile.from_days(
        {d: DayProfile.constant(17) for d in range(7)}
    )
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_SCHEDULES: {
                HT: {
                    CONF_SCHEDULE_ENTITY: "schedule.heating",
                    CONF_BASE_TEMPERATURE: 17,
                    CONF_DEFAULT_TEMPERATURE: 21,
                }
            }
        },
    )
    await hass.async_block_till_done()
    # The reload restores the device (incl. the known profile) from storage.
    hub = config_entry.runtime_data
    # The initial sync is debounced by a few seconds.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await hass.async_block_till_done()

    frames = [m for m in stick.transmitted if m.type is MessageType.CONFIG_WEEK_PROFILE]
    assert len(frames) == 1  # only Monday differs from the known profile
    assert frames[0].payload[0] == 2  # Monday in the device's day order
    monday = hub.devices[HT].week_profile.days[0]
    assert monday == DayProfile.from_points([("06:00", 17), ("08:30", 22), ("24:00", 17)])
    assert not ir.async_get(hass).issues
