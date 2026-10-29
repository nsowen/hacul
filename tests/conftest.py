# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixtures: Home Assistant with an emulated culfw stick."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
from unittest.mock import patch

from pycul.protocols.max import MaxMessage, MessageType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cul.const import (
    CONF_BAUDRATE,
    CONF_FAKE_SC_ADDRESS,
    CONF_FAKE_WT_ADDRESS,
    CONF_MAX_ADDRESS,
    CONF_NAME,
    CONF_PROTOCOL,
    CONF_STICKS,
    CONF_URL,
    DOMAIN,
)

ME = "123456"
HT = "0a1b2c"
DEVICE_PATH = "/dev/serial/by-id/usb-busware.de_CUL868-if00"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/cul."""


class FakeWriter:
    def __init__(self, stick: FakeStick) -> None:
        self._stick = stick
        self._buffer = b""

    def write(self, data: bytes) -> None:
        self._buffer += data
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            self._stick.on_command(line.decode())

    def close(self) -> None:
        self._stick.reader.feed_eof()

    async def wait_closed(self) -> None:
        return None


class FakeStick:
    """A culfw stick that acknowledges every MAX! frame like a thermostat."""

    def __init__(self) -> None:
        self.reader: asyncio.StreamReader | None = None
        self.commands: list[str] = []
        self.transmitted: list[MaxMessage] = []
        self.credits = 900
        self.fail_open = False
        #: Payload of the Ack (status + thermostat state).
        self.ack_payload = "011900AA"
        self.respond: Callable[[MaxMessage], list[str]] | None = None

    def emit(self, line: str) -> None:
        assert self.reader is not None
        self.reader.feed_data(f"{line}\r\n".encode())

    def on_command(self, command: str) -> None:
        self.commands.append(command)
        if command == "V":
            self.emit("V 1.67 CUL868")
        elif command == "X":
            self.emit(f"21  {self.credits}")
        elif command.startswith(("Zs", "Zf")):
            message = MaxMessage.from_line("Z" + command[2:])
            self.transmitted.append(message)
            replies = (
                self.respond(message)
                if self.respond
                else [
                    frame(
                        MessageType.ACK, message.dst, message.src, self.ack_payload, message.counter
                    )
                ]
            )
            loop = asyncio.get_running_loop()
            for reply in replies:
                loop.call_later(0.01, self.emit, reply)

    async def open(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if self.fail_open:
            raise OSError("No such device")
        self.reader = asyncio.StreamReader()
        return self.reader, FakeWriter(self)


def frame(msg_type: MessageType, src: str, dst: str, payload: str, counter: int = 1) -> str:
    """A received frame as culfw prints it (with RSSI byte)."""
    message = MaxMessage(counter, 0, msg_type, src, dst, 0, bytes.fromhex(payload))
    return "Z" + message.to_hex() + "40"


@pytest.fixture
def stick() -> Generator[FakeStick]:
    """Patch the serial transport of pycul with an emulated stick."""
    fake = FakeStick()
    with patch("serial_asyncio_fast.open_serial_connection", fake.open):
        yield fake


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A configured MAX! network with one stick."""
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
    )
