# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for unplugging and re-plugging the stick."""

from __future__ import annotations

import asyncio

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pycul import cul as cul_module
from pycul.protocols.max import MessageType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import HT, ME, FakeStick, frame

CLIMATE = "climate.max_radiator_thermostat_0a1b2c"
CONNECTED = "binary_sensor.max_123456_stick_cul"


async def wait_for_state(hass: HomeAssistant, entity_id: str, state: str) -> None:
    """pycul reconnects in its own tasks, which async_block_till_done does not track."""
    for _ in range(100):
        await hass.async_block_till_done()
        if hass.states.get(entity_id).state == state:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{entity_id} is {hass.states.get(entity_id).state}, not {state}")


async def test_unplug_and_replug(
    hass: HomeAssistant,
    stick: FakeStick,
    config_entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A long back-off: the quick reconnect must come from the USB event.
    monkeypatch.setattr(cul_module, "RECONNECT_MIN_DELAY", 3600.0)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    stick.emit(frame(MessageType.THERMOSTAT_STATE, HT, ME, "19202800D700"))
    await hass.async_block_till_done()

    stick.fail_open = True
    stick.reader.feed_eof()  # unplugged
    await wait_for_state(hass, CONNECTED, "off")
    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE

    stick.fail_open = False
    hub = config_entry.runtime_data
    hub._on_usb_event({object()}, set())  # Home Assistant saw a USB device appear
    await wait_for_state(hass, CONNECTED, "on")
    assert hass.states.get(CLIMATE).state == "heat"
    # The re-opened port was initialised again.
    assert stick.commands.count(f"Za{ME}") == 2
