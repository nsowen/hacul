# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Sensors of MAX! devices and of the CUL sticks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
import homeassistant.util.dt as dt_util
from pycul import Cul
from pycul.protocols.max import DeviceType, MaxDevice, Mode

from . import CulConfigEntry
from .entity import CulDeviceEntity, CulHubEntity, device_time_to_aware, setup_device_entities
from .hub import CulHub

PARALLEL_UPDATES = 0

#: Schedule based sensors change with the clock, not with radio messages.
SCHEDULE_REFRESH = timedelta(minutes=1)


def _now() -> datetime:
    return dt_util.now().replace(tzinfo=None)


def _temporary_until(device: MaxDevice) -> datetime | None:
    if device.state.mode is not Mode.TEMPORARY or device.state.until is None:
        return None
    return device_time_to_aware(device.state.until)


def _next_change(device: MaxDevice) -> datetime | None:
    if device.week_profile is None:
        return None
    switch = device.week_profile.next_switch(_now())
    return device_time_to_aware(switch[0]) if switch else None


@dataclass(frozen=True, kw_only=True)
class MaxSensorDescription(SensorEntityDescription):
    """Describes a MAX! device sensor."""

    value_fn: Callable[[MaxDevice], float | int | str | datetime | None]
    exists_fn: Callable[[MaxDevice], bool]
    follows_schedule: bool = False


DEVICE_SENSORS: tuple[MaxSensorDescription, ...] = (
    MaxSensorDescription(
        key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.state.current_temperature,
        exists_fn=lambda d: d.device_type.is_thermostat,
    ),
    MaxSensorDescription(
        key="valve_position",
        translation_key="valve_position",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda d: d.state.valve_position,
        exists_fn=lambda d: (
            d.device_type in (DeviceType.HEATING_THERMOSTAT, DeviceType.HEATING_THERMOSTAT_PLUS)
        ),
    ),
    MaxSensorDescription(
        key="mode",
        translation_key="mode",
        device_class=SensorDeviceClass.ENUM,
        options=[mode.name.lower() for mode in Mode],
        value_fn=lambda d: d.state.mode.name.lower() if d.state.mode is not None else None,
        exists_fn=lambda d: d.device_type.is_thermostat,
    ),
    MaxSensorDescription(
        key="temporary_until",
        translation_key="temporary_until",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_temporary_until,
        exists_fn=lambda d: d.device_type.is_thermostat,
    ),
    MaxSensorDescription(
        key="scheduled_temperature",
        translation_key="scheduled_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.week_profile.target_at(_now()) if d.week_profile else None,
        exists_fn=lambda d: d.device_type.is_thermostat,
        follows_schedule=True,
    ),
    MaxSensorDescription(
        key="next_schedule_change",
        translation_key="next_schedule_change",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_next_change,
        exists_fn=lambda d: d.device_type.is_thermostat,
        follows_schedule=True,
    ),
    MaxSensorDescription(
        key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.state.rssi,
        exists_fn=lambda d: True,
    ),
    MaxSensorDescription(
        key="group",
        translation_key="group",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.group,
        exists_fn=lambda d: True,
    ),
    MaxSensorDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.state.last_seen,
        exists_fn=lambda d: True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CulConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add device sensors, one airtime sensor per stick and the queue sensor."""
    hub = entry.runtime_data
    async_add_entities([CulQueueSensor(hub)] + [CulAirtimeSensor(hub, cul) for cul in hub.culs])
    # With several sticks, the reception per stick shows which one covers a device.
    per_stick = hub.culs if len(hub.culs) > 1 else []
    setup_device_entities(
        hass,
        hub,
        async_add_entities,
        lambda device: [
            *(
                MaxSensor(hub, device, description)
                for description in DEVICE_SENSORS
                if description.exists_fn(device)
            ),
            *(MaxStickRssiSensor(hub, device, cul) for cul in per_stick),
        ],
    )


class MaxSensor(CulDeviceEntity, SensorEntity):
    """A value reported by (or derived for) a MAX! device."""

    entity_description: MaxSensorDescription

    def __init__(self, hub: CulHub, device: MaxDevice, description: MaxSensorDescription) -> None:
        super().__init__(hub, device, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        """Refresh schedule based sensors every minute."""
        await super().async_added_to_hass()
        if self.entity_description.follows_schedule:
            self.async_on_remove(
                async_track_time_interval(self.hass, self._refresh, SCHEDULE_REFRESH)
            )

    @callback
    def _refresh(self, _now: datetime) -> None:
        # Must be a callback: a plain function would run in a worker thread.
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | int | str | datetime | None:
        return self.entity_description.value_fn(self.device)

    def _state_key(self) -> object:
        return self.native_value


class MaxStickRssiSensor(CulDeviceEntity, SensorEntity):
    """Signal strength of a device as received by one stick."""

    _attr_translation_key = "rssi_stick"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, hub: CulHub, device: MaxDevice, cul: Cul) -> None:
        super().__init__(hub, device, f"rssi_{cul.name}")
        self.cul = cul
        self._attr_translation_placeholders = {"stick": cul.name}

    @property
    def native_value(self) -> float | None:
        return self.device.rssi_by_cul.get(self.cul.name)

    def _state_key(self) -> object:
        return self.native_value


class CulAirtimeSensor(CulHubEntity, SensorEntity):
    """Remaining send time of a stick (1% duty cycle, max 9 s, +1 s per 100 s)."""

    _attr_translation_key = "airtime"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 1

    def __init__(self, hub: CulHub, cul: Cul) -> None:
        super().__init__(hub, f"airtime_{cul.name}")
        self.cul = cul
        self._attr_translation_placeholders = {"stick": cul.name}

    @property
    def available(self) -> bool:
        return self.cul.connected

    @property
    def native_value(self) -> float | None:
        return self.cul.credits / 100 if self.cul.credits is not None else None


class CulQueueSensor(CulHubEntity, SensorEntity):
    """Number of radio messages waiting to be sent."""

    _attr_translation_key = "send_queue"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: CulHub) -> None:
        super().__init__(hub, "send_queue")

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> int:
        return len(self.hub.protocol.queue)
