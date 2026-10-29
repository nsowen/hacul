# Author: Nils Sowen <n.sowen@2scale.net>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Services of the CUL integration."""

from __future__ import annotations

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.core import HomeAssistant, SupportsResponse, callback
from homeassistant.helpers import config_validation as cv, service
from pycul.protocols.max.weekprofile import WEEKDAY_NAMES
import voluptuous as vol

from .const import DOMAIN

SERVICE_SET_WEEK_PROFILE = "set_week_profile"
SERVICE_GET_WEEK_PROFILE = "get_week_profile"
SERVICE_SET_TEMPORARY_TEMPERATURE = "set_temporary_temperature"
SERVICE_SET_GROUP = "set_group"

_TEMPERATURE = vol.All(vol.Coerce(float), vol.Range(min=4.5, max=30.5))
_PERIOD = vol.Schema(
    {
        vol.Required("from"): cv.string,
        vol.Required("to"): cv.string,
        vol.Required("temperature"): _TEMPERATURE,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the entity services of the thermostats."""
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SET_WEEK_PROFILE,
        entity_domain=CLIMATE_DOMAIN,
        schema={
            vol.Required("profile"): vol.Schema(
                {vol.Optional(day): [_PERIOD] for day in WEEKDAY_NAMES}
            ),
            vol.Optional("base_temperature"): _TEMPERATURE,
        },
        func="async_set_week_profile",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_GET_WEEK_PROFILE,
        entity_domain=CLIMATE_DOMAIN,
        schema=None,
        func="async_get_week_profile",
        supports_response=SupportsResponse.ONLY,
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SET_TEMPORARY_TEMPERATURE,
        entity_domain=CLIMATE_DOMAIN,
        schema={
            vol.Required("temperature"): _TEMPERATURE,
            vol.Required("until"): cv.datetime,
        },
        func="async_set_temporary_temperature",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SET_GROUP,
        entity_domain=CLIMATE_DOMAIN,
        schema={vol.Required("group"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255))},
        func="async_set_group",
    )
