# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for the config and options flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USB, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.usb import UsbServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cul.const import (
    CONF_BAUDRATE,
    CONF_MAX_ADDRESS,
    CONF_STICKS,
    CONF_URL,
    DOMAIN,
)

from .conftest import DEVICE_PATH, ME, FakeStick


def _no_ports():
    return patch("serial.tools.list_ports.comports", return_value=[])


async def test_user_flow_creates_entry(hass: HomeAssistant, stick: FakeStick) -> None:
    with _no_ports():
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: DEVICE_PATH, CONF_BAUDRATE: 38400, CONF_MAX_ADDRESS: "12AB56"},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "MAX! 12ab56"
    assert result["data"][CONF_MAX_ADDRESS] == "12ab56"
    assert result["data"][CONF_STICKS][0][CONF_URL] == DEVICE_PATH
    assert result["result"].unique_id == "12ab56"


async def test_user_flow_errors(hass: HomeAssistant, stick: FakeStick) -> None:
    with _no_ports():
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: DEVICE_PATH, CONF_BAUDRATE: 38400, CONF_MAX_ADDRESS: "xyz"},
        )
        assert result["errors"] == {CONF_MAX_ADDRESS: "invalid_address"}
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: DEVICE_PATH, CONF_BAUDRATE: 38400, CONF_MAX_ADDRESS: "111111"},
        )
        assert result["errors"] == {CONF_MAX_ADDRESS: "reserved_address"}
        stick.fail_open = True
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: DEVICE_PATH, CONF_BAUDRATE: 38400, CONF_MAX_ADDRESS: ME},
        )
        assert result["errors"] == {"base": "cannot_connect"}


async def test_same_network_only_once(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    with _no_ports():
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: "/dev/ttyACM1", CONF_BAUDRATE: 38400, CONF_MAX_ADDRESS: ME},
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_usb_discovery(hass: HomeAssistant, stick: FakeStick) -> None:
    info = UsbServiceInfo(
        device="/dev/ttyACM0",
        vid="03EB",
        pid="204B",
        serial_number="1234",
        manufacturer="busware.de",
        description="CUL868",
    )
    with patch("homeassistant.components.usb.get_serial_by_id", return_value=DEVICE_PATH):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USB}, data=info
        )
    assert result["step_id"] == "usb_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MAX_ADDRESS: ME}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_STICKS][0][CONF_URL] == DEVICE_PATH


async def test_options_add_stick(
    hass: HomeAssistant, stick: FakeStick, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    with _no_ports():
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "add_stick"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_URL: "socket://192.168.1.10:2323", CONF_BAUDRATE: 38400, "name": "attic"},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert [s["name"] for s in config_entry.data[CONF_STICKS]] == ["cul", "attic"]
    await hass.config_entries.async_unload(config_entry.entry_id)
