# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Constants for the CUL integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "cul"

# Config entry data
CONF_STICKS: Final = "sticks"
CONF_URL: Final = "url"
CONF_BAUDRATE: Final = "baudrate"
CONF_NAME: Final = "name"
CONF_PROTOCOL: Final = "protocol"
CONF_MAX_ADDRESS: Final = "max_address"
CONF_FAKE_WT_ADDRESS: Final = "fake_wt_address"
CONF_FAKE_SC_ADDRESS: Final = "fake_sc_address"

PROTOCOL_MAX: Final = "max"

# Options
CONF_ADOPT_DEVICES: Final = "adopt_devices"
CONF_FAST_SEND: Final = "fast_send_after_wake_up"
CONF_SCHEDULES: Final = "schedules"
CONF_SCHEDULE_ENTITY: Final = "schedule_entity"
CONF_BASE_TEMPERATURE: Final = "base_temperature"
CONF_DEFAULT_TEMPERATURE: Final = "default_temperature"
CONF_EXTERNAL: Final = "external_sensors"
CONF_TEMPERATURE_SENSOR: Final = "temperature_sensor"
CONF_WINDOW_SENSOR: Final = "window_sensor"
CONF_SEND_INTERVAL: Final = "send_interval"

DEFAULT_BAUDRATE: Final = 38400
DEFAULT_MAX_ADDRESS: Final = "123456"
DEFAULT_FAKE_WT_ADDRESS: Final = "111111"
DEFAULT_FAKE_SC_ADDRESS: Final = "222222"
DEFAULT_BASE_TEMPERATURE: Final = 17.0
DEFAULT_SCHEDULE_TEMPERATURE: Final = 21.0
#: Minimum seconds between two fake wall thermostat updates of one thermostat.
#: One update costs ~1.1 s of the 36 s per hour a stick may send.
DEFAULT_SEND_INTERVAL: Final = 600
PAIRING_DURATION: Final = 60

#: busware CUL USB ids (Atmel VID).
CUL_USB_VID: Final = "03EB"
CUL_USB_PID: Final = "204B"

STORAGE_VERSION: Final = 1
STORAGE_SAVE_DELAY: Final = 10
CREDIT_REFRESH_INTERVAL: Final = timedelta(minutes=5)
SCHEDULE_POLL_INTERVAL: Final = timedelta(minutes=15)


def signal_device_added(entry_id: str) -> str:
    """Dispatcher signal for a newly paired or adopted device."""
    return f"{DOMAIN}_{entry_id}_device_added"


def signal_hub_updated(entry_id: str) -> str:
    """Dispatcher signal for connection, credit or queue changes (hub entities)."""
    return f"{DOMAIN}_{entry_id}_hub_updated"


def signal_connection(entry_id: str) -> str:
    """Dispatcher signal for stick connection changes (availability of all entities)."""
    return f"{DOMAIN}_{entry_id}_connection"


def signal_zones_changed(entry_id: str) -> str:
    """Dispatcher signal for a changed group id of any device (zone entities)."""
    return f"{DOMAIN}_{entry_id}_zones_changed"
