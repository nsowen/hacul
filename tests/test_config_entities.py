# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for the write-only configuration numbers and selects."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from homeassistant.components.number import ATTR_VALUE, DOMAIN as NUMBER_DOMAIN, SERVICE_SET_VALUE
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pycul.protocols.max import MessageType, TemperatureConfig, ValveConfig
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from .conftest import HT, ME, FakeStick, frame

WT = "0e0e0e"
COMFORT = "number.max_radiator_thermostat_0a1b2c_comfort_temperature"
WINDOW_TEMP = "number.max_radiator_thermostat_0a1b2c_window_open_temperature"
WINDOW_MINUTES = "number.max_radiator_thermostat_0a1b2c_window_open_duration"
MAX_VALVE = "number.max_radiator_thermostat_0a1b2c_maximum_valve_setting"
BOOST = "select.max_radiator_thermostat_0a1b2c_boost_duration"
DECALC_DAY = "select.max_radiator_thermostat_0a1b2c_decalcification_day"


async def _setup(hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    stick.emit(frame(MessageType.THERMOSTAT_STATE, HT, ME, "19202800D700"))
    await hass.async_block_till_done()


async def _set_number(hass: HomeAssistant, entity_id: str, value: float) -> None:
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: value},
        blocking=True,
    )
    await hass.async_block_till_done()


async def test_config_entities_show_factory_defaults(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, stick, config_entry)
    assert float(hass.states.get(COMFORT).state) == 21.0
    assert float(hass.states.get(WINDOW_TEMP).state) == 12.0
    assert float(hass.states.get(WINDOW_MINUTES).state) == 15.0
    assert hass.states.get(BOOST).state == "5"
    assert hass.states.get(DECALC_DAY).state == "Sat"


async def test_one_number_writes_the_whole_block(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, stick, config_entry)
    stick.transmitted.clear()
    await _set_number(hass, WINDOW_TEMP, 7)
    [sent] = stick.transmitted
    assert sent.type is MessageType.CONFIG_TEMPERATURES
    assert sent.payload == TemperatureConfig(window_open_temperature=7.0).encode()
    assert float(hass.states.get(WINDOW_TEMP).state) == 7.0

    # The second change keeps the first: the radio message carries the whole block.
    stick.transmitted.clear()
    await _set_number(hass, COMFORT, 22.0)
    [sent] = stick.transmitted
    assert sent.payload == TemperatureConfig(comfort=22.0, window_open_temperature=7.0).encode()


async def test_valve_settings_keep_the_other_settings(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, stick, config_entry)
    stick.transmitted.clear()
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: BOOST, ATTR_OPTION: "30"},
        blocking=True,
    )
    await hass.async_block_till_done()
    [sent] = stick.transmitted
    assert sent.type is MessageType.CONFIG_VALVE
    assert sent.payload == ValveConfig(boost_duration=30).encode()
    assert hass.states.get(BOOST).state == "30"

    stick.transmitted.clear()
    await _set_number(hass, MAX_VALVE, 80)
    [sent] = stick.transmitted
    assert sent.payload == ValveConfig(boost_duration=30, max_valve_setting=80).encode()


async def test_wall_thermostat_has_no_valve_settings(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, stick, config_entry)
    stick.emit(frame(MessageType.WALL_THERMOSTAT_STATE, WT, ME, "19002A00000000D2", counter=2))
    await hass.async_block_till_done()
    assert (
        float(hass.states.get("number.max_wall_thermostat_0e0e0e_comfort_temperature").state) == 21
    )
    assert not hass.states.get("select.max_wall_thermostat_0e0e0e_boost_duration")
    assert not hass.states.get("number.max_wall_thermostat_0e0e0e_max_valve_setting")


async def test_changes_while_a_frame_is_on_air_merge(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, stick, config_entry)
    hub = config_entry.runtime_data
    stick.transmitted.clear()
    await asyncio.gather(
        hub.async_configure_temperatures(HT, window_open_temperature=7),
        hub.async_configure_temperatures(HT, comfort=22.0),
    )
    config = hub.temperature_config(HT)
    assert (config.comfort, config.window_open_temperature) == (22.0, 7.0)
    # However the two sends interleave, the last frame carries both changes.
    assert stick.transmitted[-1].payload == TemperatureConfig(
        comfort=22.0, window_open_temperature=7.0
    ).encode()


async def test_written_config_survives_a_reload(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, stick, config_entry)
    await _set_number(hass, WINDOW_TEMP, 7)
    # The store saves with a delay; let it flush before the reload.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=2))
    await hass.async_block_till_done(wait_background_tasks=True)

    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get(WINDOW_TEMP).state) == 7.0
