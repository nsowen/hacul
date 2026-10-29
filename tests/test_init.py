# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for setup, entities and services."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.climate import (
    ATTR_HVAC_ACTION,
    ATTR_PRESET_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pycul.protocols.max import MessageType
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.cul.const import CONF_EXTERNAL, CONF_TEMPERATURE_SENSOR, DOMAIN

from .conftest import HT, ME, FakeStick, frame

CLIMATE = "climate.max_radiator_thermostat_0a1b2c"


async def _setup_with_thermostat(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    # manual, valve 32 %, target 20.0, measured 21.5
    stick.emit(frame(MessageType.THERMOSTAT_STATE, HT, ME, "19202800D700"))
    await hass.async_block_till_done()


async def test_setup_and_unload(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert stick.commands[:5] == ["", "V", "X21", "Zr", f"Za{ME}"]
    assert hass.states.get("button.max_123456_start_pairing") is not None
    assert hass.states.get("binary_sensor.max_123456_stick_cul").state == "on"

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_retries_without_stick(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    stick.fail_open = True
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_adopted_thermostat_entities(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    await _setup_with_thermostat(hass, stick, config_entry)
    state = hass.states.get(CLIMATE)
    assert state is not None
    assert state.state == HVACMode.HEAT
    assert state.attributes[ATTR_TEMPERATURE] == 20.0
    assert state.attributes["current_temperature"] == 21.5
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING
    assert hass.states.get("sensor.max_radiator_thermostat_0a1b2c_valve_position").state == "32"
    assert hass.states.get("sensor.max_radiator_thermostat_0a1b2c_temperature").state == "21.5"
    entity = er.async_get(hass).async_get("sensor.max_radiator_thermostat_0a1b2c_signal_strength")
    assert entity is not None and entity.disabled


async def test_climate_commands(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    await _setup_with_thermostat(hass, stick, config_entry)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 21.0},
        blocking=True,
    )
    assert stick.transmitted[-1].payload == bytes([42 | 0x40])  # manual 21.0

    stick.ack_payload = "01180020"  # from now on the device reports auto, 16.0
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: CLIMATE, "hvac_mode": HVACMode.AUTO},
        blocking=True,
    )
    assert stick.transmitted[-1].payload == b"\x00"
    assert hass.states.get(CLIMATE).state == HVACMode.AUTO

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 22.5},
        blocking=True,
    )
    # stays in auto until the next switch point
    assert stick.transmitted[-1].payload == bytes([45])

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_PRESET_MODE: "boost"},
        blocking=True,
    )
    assert stick.transmitted[-1].payload == b"\xc0"

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: CLIMATE, "hvac_mode": HVACMode.OFF},
        blocking=True,
    )
    assert stick.transmitted[-1].payload == bytes([9 | 0x40])  # manual 4.5


async def test_week_profile_services(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    await _setup_with_thermostat(hass, stick, config_entry)

    response = await hass.services.async_call(
        DOMAIN, "get_week_profile", {ATTR_ENTITY_ID: CLIMATE}, blocking=True, return_response=True
    )
    assert response[CLIMATE]["known"] is False

    await hass.services.async_call(
        DOMAIN,
        "set_week_profile",
        {
            ATTR_ENTITY_ID: CLIMATE,
            "profile": {"monday": [{"from": "06:00", "to": "08:30", "temperature": 21}]},
            "base_temperature": 17,
        },
        blocking=True,
    )
    profile_frames = [m for m in stick.transmitted if m.type is MessageType.CONFIG_WEEK_PROFILE]
    assert len(profile_frames) == 7  # unknown profile: every day once

    response = await hass.services.async_call(
        DOMAIN, "get_week_profile", {ATTR_ENTITY_ID: CLIMATE}, blocking=True, return_response=True
    )
    monday = response[CLIMATE]["profile"]["monday"]
    assert monday[1] == {"from": "06:00", "to": "08:30", "temperature": 21.0}
    assert hass.states.get(
        "sensor.max_radiator_thermostat_0a1b2c_next_schedule_change"
    ).state not in ("unknown", "unavailable")


async def test_external_temperature_sensor(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    hass.states.async_set("sensor.living_room", "19.6", {"device_class": "temperature"})
    await _setup_with_thermostat(hass, stick, config_entry)

    hass.config_entries.async_update_entry(
        config_entry,
        options={CONF_EXTERNAL: {HT: {CONF_TEMPERATURE_SENSOR: "sensor.living_room"}}},
    )
    await hass.async_block_till_done()
    # After the reload the set point is unknown; the value goes out once it is reported.
    controls = [m for m in stick.transmitted if m.type is MessageType.WALL_THERMOSTAT_CONTROL]
    assert not controls
    stick.emit(frame(MessageType.THERMOSTAT_STATE, HT, ME, "19202800D700", counter=2))
    await hass.async_block_till_done()

    links = [m for m in stick.transmitted if m.type is MessageType.ADD_LINK_PARTNER]
    assert links and links[0].payload.hex() == "11111103"
    controls = [m for m in stick.transmitted if m.type is MessageType.WALL_THERMOSTAT_CONTROL]
    assert controls and controls[-1].src == "111111"
    assert controls[-1].payload[1] == 196  # 19.6 degree


async def test_schedule_sensors_refresh_in_the_event_loop(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    await _setup_with_thermostat(hass, stick, config_entry)
    # The minute timer must not write state from a worker thread (HA raises then).
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=2))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.max_radiator_thermostat_0a1b2c_scheduled_temperature")


async def test_devices_hang_below_the_cul(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    await _setup_with_thermostat(hass, stick, config_entry)
    registry = dr.async_get(hass)
    hub = registry.async_get_device(identifiers={(DOMAIN, config_entry.entry_id)})
    thermostat = registry.async_get_device(identifiers={(DOMAIN, HT)})
    assert hub is not None and thermostat is not None
    assert thermostat.via_device_id == hub.id == config_entry.runtime_data.device_id
