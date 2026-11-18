# SPDX-License-Identifier: GPL-2.0-or-later
"""Quiet thermostats are asked for their state with a benign time frame.

Field-verified: thermostats do not report local operation (the knob, the
boost button) over the air, but every Ack carries the full state - so the
periodic time frame doubles as a state query. Window contacts are skipped:
they only receive frames right after they transmit.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pycul.protocols.max import DeviceType, MaxDevice, MessageType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cul.const import (
    CONF_BAUDRATE,
    CONF_FAKE_SC_ADDRESS,
    CONF_FAKE_WT_ADDRESS,
    CONF_MAX_ADDRESS,
    CONF_NAME,
    CONF_PROTOCOL,
    CONF_STATE_REFRESH_INTERVAL,
    CONF_STICKS,
    CONF_URL,
    DOMAIN,
)
from custom_components.cul.hub import CulHub

from .conftest import DEVICE_PATH, HT, ME, FakeStick, frame

WT = "0d0e0f"
SC = "019b8f"


def _entry(options: dict[str, object]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"MAX! {ME}",
        unique_id=ME,
        data={
            CONF_PROTOCOL: "max",
            CONF_MAX_ADDRESS: ME,
            CONF_FAKE_WT_ADDRESS: "111111",
            CONF_FAKE_SC_ADDRESS: "222222",
            CONF_STICKS: [{CONF_URL: DEVICE_PATH, CONF_BAUDRATE: 38400, CONF_NAME: "cul"}],
        },
        options=options,
    )


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A network that probes quiet thermostats once a minute."""
    return _entry({CONF_STATE_REFRESH_INTERVAL: 60})


async def _setup(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> CulHub:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    stick.transmitted.clear()
    return config_entry.runtime_data


async def test_quiet_thermostat_is_probed_and_refreshed(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    hub = await _setup(hass, stick, config_entry)
    hub.protocol.add_device(MaxDevice(HT, DeviceType.HEATING_THERMOSTAT))
    await hub._refresh_states(None)
    [probe] = stick.transmitted
    assert probe.type is MessageType.TIME_INFORMATION
    assert probe.dst == HT
    # The Ack carries the state, so the probe is also the refresh.
    device = hub.devices[HT]
    assert device.state.mode is not None
    assert device.state.last_seen is not None
    await hub._refresh_states(None)
    assert stick.transmitted == [probe]  # now fresh: not probed again


async def test_recently_heard_thermostat_is_not_probed(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    hub = await _setup(hass, stick, config_entry)
    stick.emit(frame(MessageType.THERMOSTAT_STATE, HT, ME, "19202800D700"))
    await hass.async_block_till_done()
    await hub._refresh_states(None)
    assert stick.transmitted == []


async def test_window_contacts_are_never_probed(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    hub = await _setup(hass, stick, config_entry)
    hub.protocol.add_device(MaxDevice(HT, DeviceType.HEATING_THERMOSTAT))
    hub.protocol.add_device(MaxDevice(WT, DeviceType.WALL_THERMOSTAT))
    hub.protocol.add_device(MaxDevice(SC, DeviceType.SHUTTER_CONTACT))
    await hub._refresh_states(None)
    assert {probe.dst for probe in stick.transmitted} == {HT, WT}
    assert all(probe.type is MessageType.TIME_INFORMATION for probe in stick.transmitted)


async def test_probing_can_be_disabled(hass: HomeAssistant, stick: FakeStick) -> None:
    hub = await _setup(hass, stick, _entry({CONF_STATE_REFRESH_INTERVAL: 0}))
    hub.protocol.add_device(MaxDevice(HT, DeviceType.HEATING_THERMOSTAT))
    await hub._refresh_states(None)
    assert stick.transmitted == []
