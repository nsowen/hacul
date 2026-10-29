# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Feed Home Assistant sensors into MAX! thermostats.

A temperature sensor is sent through the CUL's fake wall thermostat, so the
radiator thermostat regulates on the room temperature instead of the value
measured next to the radiator. A window binary sensor is sent through the
fake window contact, so the thermostat switches to its window-open
temperature.

Every frame costs about 1.1 s of the 36 s per hour a stick may send, so
temperature updates are rate limited (configurable, default 10 minutes) and
only sent when the value changed by at least 0.1 degree. Window changes are
sent immediately.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from pycul import CulError
from pycul.protocols.max import Mode

from .const import (
    CONF_EXTERNAL,
    CONF_SEND_INTERVAL,
    CONF_TEMPERATURE_SENSOR,
    CONF_WINDOW_SENSOR,
    DEFAULT_SEND_INTERVAL,
)

if TYPE_CHECKING:
    from .hub import CulHub

_LOGGER = logging.getLogger(__name__)

MIN_CHANGE = 0.1


class _TemperatureFeed:
    """Rate limited forwarding of one temperature sensor to one thermostat."""

    def __init__(self, hass: HomeAssistant, hub: CulHub, address: str, interval: float) -> None:
        self.hass = hass
        self.hub = hub
        self.address = address
        self.interval = interval
        self._last_sent: float | None = None
        self._last_sent_at = 0.0
        self._pending: float | None = None
        self._cancel_timer: Callable[[], None] | None = None

    @callback
    def update(self, value: float) -> None:
        self._pending = value
        if self._last_sent is not None and abs(value - self._last_sent) < MIN_CHANGE:
            return
        wait = self._last_sent_at + self.interval - time.monotonic()
        if wait <= 0:
            self.hass.async_create_task(self._send())
        elif self._cancel_timer is None:
            self._cancel_timer = async_call_later(self.hass, wait, self._timer_fired)

    @callback
    def device_updated(self) -> None:
        """Deliver a value that waited for the thermostat's set point to become known."""
        if self._last_sent is None and self._pending is not None and self._cancel_timer is None:
            device = self.hub.devices.get(self.address)
            if device is not None and device.state.target_temperature is not None:
                self.update(self._pending)

    @callback
    def _timer_fired(self, _now: Any) -> None:
        self._cancel_timer = None
        self.hass.async_create_task(self._send())

    async def _send(self) -> None:
        value = self._pending
        device = self.hub.devices.get(self.address)
        if value is None or device is None or not self.hub.available:
            return
        target = device.state.target_temperature
        if target is None or device.state.mode is Mode.BOOST:
            return  # do not overwrite an unknown set point or a running boost
        self._last_sent_at = time.monotonic()
        try:
            await self.hub.protocol.send_fake_wall_thermostat(self.address, target, value)
        except CulError as err:
            _LOGGER.debug("Room temperature for %s not delivered: %s", self.address, err)
            return
        self._last_sent = value

    @callback
    def stop(self) -> None:
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None


class ExternalSensorBridge:
    """Forwards the configured HA sensors to the thermostats."""

    def __init__(self, hass: HomeAssistant, hub: CulHub) -> None:
        self.hass = hass
        self.hub = hub
        self._config: dict[str, dict[str, Any]] = hub.entry.options.get(CONF_EXTERNAL, {})

    @callback
    def async_start(self) -> Callable[[], None]:
        """Start listening; returns a function that stops it."""
        unsubscribe: list[Callable[[], None]] = []
        feeds: list[_TemperatureFeed] = []
        for address, config in self._config.items():
            if sensor := config.get(CONF_TEMPERATURE_SENSOR):
                feed = _TemperatureFeed(
                    self.hass,
                    self.hub,
                    address,
                    config.get(CONF_SEND_INTERVAL, DEFAULT_SEND_INTERVAL),
                )
                feeds.append(feed)
                if device := self.hub.devices.get(address):
                    unsubscribe.append(
                        device.add_listener(lambda _d, feed=feed: feed.device_updated())
                    )
                unsubscribe.append(
                    async_track_state_change_event(
                        self.hass, [sensor], self._temperature_listener(feed)
                    )
                )
                state = self.hass.states.get(sensor)
                if state is not None and (value := _as_float(state.state)) is not None:
                    feed.update(value)
            if window := config.get(CONF_WINDOW_SENSOR):
                unsubscribe.append(
                    async_track_state_change_event(
                        self.hass, [window], self._window_listener(address)
                    )
                )

        def stop() -> None:
            for remove in unsubscribe:
                remove()
            for feed in feeds:
                feed.stop()

        return stop

    @staticmethod
    def _temperature_listener(
        feed: _TemperatureFeed,
    ) -> Callable[[Event[EventStateChangedData]], None]:
        @callback
        def listener(event: Event[EventStateChangedData]) -> None:
            state = event.data["new_state"]
            if state is not None and (value := _as_float(state.state)) is not None:
                feed.update(value)

        return listener

    def _window_listener(self, address: str) -> Callable[[Event[EventStateChangedData]], None]:
        @callback
        def listener(event: Event[EventStateChangedData]) -> None:
            new, old = event.data["new_state"], event.data["old_state"]
            if new is None or new.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                return
            if old is not None and old.state == new.state:
                return
            self.hass.async_create_task(self._send_window(address, new.state == STATE_ON))

        return listener

    async def _send_window(self, address: str, window_open: bool) -> None:
        try:
            await self.hub.protocol.send_fake_shutter_contact(address, window_open)
        except CulError as err:
            _LOGGER.warning("Window state for %s not delivered: %s", address, err)


def _as_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None
