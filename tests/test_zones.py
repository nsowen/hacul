# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for heating zones (MAX! groups), the device entities and eco switches."""

from __future__ import annotations

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import area_registry as ar, device_registry as dr
from pycul.protocols.max import DeviceType, MaxDevice, MaxMessage, MessageType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cul.config_flow import split_by_wall_thermostat
from custom_components.cul.const import DOMAIN

from .conftest import HT, ME, FakeStick, frame

HT2 = "0d1e2f"
WT = "0e0e0e"
PB = "7b7b7b"
CLIMATE_HT = "climate.max_radiator_thermostat_0a1b2c"


def group_frame(msg_type: MessageType, src: str, dst: str, payload: str, group: int) -> str:
    message = MaxMessage(5, 0x04, msg_type, src, dst, group, bytes.fromhex(payload))
    return "Z" + message.to_hex() + "40"


async def _setup(hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    stick.emit(frame(MessageType.THERMOSTAT_STATE, HT, ME, "19202800D700"))
    stick.emit(frame(MessageType.WALL_THERMOSTAT_STATE, WT, ME, "19002A00000000D2", counter=2))
    await hass.async_block_till_done()


async def test_status_entities(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, stick, config_entry)
    assert hass.states.get("sensor.max_radiator_thermostat_0a1b2c_mode").state == "manual"
    assert hass.states.get("sensor.max_radiator_thermostat_0a1b2c_heating_zone").state == "0"
    assert hass.states.get("binary_sensor.max_radiator_thermostat_0a1b2c_buttons_locked").state == (
        "off"
    )
    assert hass.states.get("switch.max_wall_thermostat_0e0e0e_show_measured_temperature")
    # Only one stick: no per-stick signal strength.
    assert not [s for s in hass.states.async_entity_ids("sensor") if "signal_strength_" in s]


async def test_learned_group_and_group_set_point(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, stick, config_entry)
    # The wall thermostat talks to the radiator thermostat with group 3.
    stick.emit(group_frame(MessageType.WALL_THERMOSTAT_CONTROL, WT, HT, "2AD2", group=3))
    stick.emit(group_frame(MessageType.THERMOSTAT_STATE, HT, WT, "19202800D700", group=3))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.max_radiator_thermostat_0a1b2c_heating_zone").state == "3"

    stick.transmitted.clear()
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: "climate.max_wall_thermostat_0e0e0e", ATTR_TEMPERATURE: 22.5},
        blocking=True,
    )
    [sent] = stick.transmitted
    assert (sent.group, sent.flags) == (3, 0x04)
    # The radiator thermostat applies the group frame too.
    state = hass.states.get("climate.max_radiator_thermostat_0a1b2c")
    assert state.attributes[ATTR_TEMPERATURE] == 22.5


async def test_room_from_area(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, stick, config_entry)
    area = ar.async_get(hass).async_create("Living room")
    devices = dr.async_get(hass)
    for address in (HT, WT):
        device = devices.async_get_device(identifiers={(DOMAIN, address)})
        devices.async_update_device(device.id, area_id=area.id)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "zone_select"}
    )
    assert result["step_id"] == "zone_select"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"zone": f"area:{area.id}:1"}
    )
    assert result["step_id"] == "zone"
    stick.transmitted.clear()
    # Defaults: the area's devices, the first free group id, linking on.
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "zone_started"
    await hass.async_block_till_done(wait_background_tasks=True)

    sent = {(m.type, m.dst, m.payload.hex()) for m in stick.transmitted}
    assert sent == {
        (MessageType.SET_GROUP_ID, HT, "01"),
        (MessageType.SET_GROUP_ID, WT, "01"),
        (MessageType.ADD_LINK_PARTNER, HT, f"{WT}03"),
        (MessageType.ADD_LINK_PARTNER, WT, f"{HT}01"),
    }
    hub = config_entry.runtime_data
    assert hub.devices[HT].group == hub.devices[WT].group == 1
    assert hub.protocol.link_clusters() == [{HT, WT}]


async def test_eco_switch_event(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, stick, config_entry)
    stick.emit(frame(MessageType.PUSH_BUTTON_STATE, PB, ME, "0001", counter=1))
    await hass.async_block_till_done()
    assert config_entry.runtime_data.devices[PB].device_type is DeviceType.PUSH_BUTTON
    entity_id = "event.max_eco_switch_7b7b7b_button"
    # The switch sends one press to each linked thermostat: one event.
    stick.emit(frame(MessageType.PUSH_BUTTON_STATE, PB, HT, "0000", counter=2))
    stick.emit(frame(MessageType.PUSH_BUTTON_STATE, PB, HT2, "0000", counter=2))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["event_type"] == "eco"
    assert config_entry.runtime_data.devices[PB].state.button_presses == 2


WT2 = "0f0f0f"
SC = "5c5c5c"


def test_split_by_wall_thermostat() -> None:
    devices = {
        WT: MaxDevice(WT, DeviceType.WALL_THERMOSTAT, link_partners={HT}),
        HT: MaxDevice(HT, DeviceType.HEATING_THERMOSTAT),
        WT2: MaxDevice(WT2, DeviceType.WALL_THERMOSTAT),
        HT2: MaxDevice(HT2, DeviceType.HEATING_THERMOSTAT, link_partners={WT2}),
        SC: MaxDevice(SC, DeviceType.SHUTTER_CONTACT, link_partners={HT, HT2}),
        "0c0c0c": MaxDevice("0c0c0c", DeviceType.HEATING_THERMOSTAT),
    }
    zones, shared = split_by_wall_thermostat(sorted(devices), devices)
    assert zones == [sorted([HT, WT]), sorted([HT2, WT2]), ["0c0c0c"]]
    assert shared == [SC]  # acts on both zones
    # One wall thermostat: the area stays one zone.
    single = {a: devices[a] for a in (WT, HT, SC)}
    assert split_by_wall_thermostat(sorted(single), single) == ([sorted(single)], [])


async def test_large_area_is_split_into_zones(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, stick, config_entry)
    stick.emit(frame(MessageType.WALL_THERMOSTAT_STATE, WT2, ME, "19002A00000000D2", counter=3))
    stick.emit(frame(MessageType.THERMOSTAT_STATE, HT2, ME, "19202800D700", counter=4))
    await hass.async_block_till_done()
    hub = config_entry.runtime_data
    hub.devices[WT].link_partners = {HT}
    hub.devices[WT2].link_partners = {HT2}
    loft = ar.async_get(hass).async_create("Loft")
    devices = dr.async_get(hass)
    for address in (HT, WT, HT2, WT2):
        device = devices.async_get_device(identifiers={(DOMAIN, address)})
        devices.async_update_device(device.id, area_id=loft.id)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "zone_select"}
    )
    options = result["data_schema"].schema["zone"].config["options"]
    assert {o["value"] for o in options} >= {f"area:{loft.id}:1", f"area:{loft.id}:2"}

    # Both wall thermostats in one zone: asks once more.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"zone": f"area:{loft.id}:1"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"devices": [HT, WT, WT2], "group": 4, "link": False}
    )
    assert result["errors"] == {"base": "several_wall_thermostats"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"devices": [HT, WT], "group": 4, "link": False},
    )
    assert result["reason"] == "zone_started"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hub.devices[HT].group == hub.devices[WT].group == 4
    assert hub.devices[WT2].group == 0


async def test_zone_climate(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, stick, config_entry)
    assert hass.states.get("climate.heating_zone_3") is None
    stick.emit(group_frame(MessageType.WALL_THERMOSTAT_CONTROL, WT, HT, "2AD2", group=3))
    stick.emit(group_frame(MessageType.THERMOSTAT_STATE, HT, WT, "19202800D700", group=3))
    await hass.async_block_till_done()

    # The wall thermostat leads: its set point and room temperature are the zone's.
    state = hass.states.get("climate.heating_zone_3")
    assert state.attributes[ATTR_TEMPERATURE] == 21.0
    assert state.attributes["current_temperature"] == 21.0
    assert state.attributes["hvac_action"] == "heating"  # valve of HT at 32 %

    stick.transmitted.clear()
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: "climate.heating_zone_3", ATTR_TEMPERATURE: 19.5},
        blocking=True,
    )
    [sent] = stick.transmitted
    assert (sent.dst, sent.group, sent.flags) == (WT, 3, 0x04)
    assert hass.states.get(CLIMATE_HT).attributes[ATTR_TEMPERATURE] == 19.5

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_group",
            {ATTR_ENTITY_ID: "climate.heating_zone_3", "group": 5},
            blocking=True,
        )
