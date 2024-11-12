"""Provides device triggers for Nuvo multi-zone amplifier (serial)."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.components.automation import (
    AutomationActionType,
    AutomationTriggerInfo,
)
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_ENTITY_ID,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.typing import ConfigType

from . import DOMAIN
from .const import DOMAIN_EVENT

TRIGGER_TYPES = ("keypad_play_pause", "keypad_prev", "keypad_next")

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_ENTITY_ID): cv.entity_id,
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
    }
)


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict]:
    """List device triggers for Nuvo multi-zone amplifier (serial) devices."""

    registry = er.async_get(hass)
    triggers = []

    # Get all the integrations entities for this device
    for entry in er.async_entries_for_device(registry, device_id):
        # if entry.domain != DOMAIN and entry.platform != "media_player":
        if entry.platform != DOMAIN or entry.domain != "media_player":
            continue

        for trigger_type in TRIGGER_TYPES:
            triggers.append(
                {
                    CONF_PLATFORM: "device",
                    CONF_DEVICE_ID: device_id,
                    CONF_DOMAIN: DOMAIN,
                    CONF_ENTITY_ID: entry.entity_id,
                    CONF_TYPE: trigger_type,
                }
            )

    return triggers


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: AutomationActionType,
    automation_info: AutomationTriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a trigger."""
    # TODO Implement your own logic to attach triggers.
    # Use the existing state or event triggers from the automation integration.

    # if config[CONF_TYPE] == "turned_on":
    #     to_state = STATE_ON
    # else:
    #     to_state = STATE_OFF

    # state_config = {
    #     state.CONF_PLATFORM: "state",
    #     CONF_ENTITY_ID: config[CONF_ENTITY_ID],
    #     state.CONF_TO: to_state,
    # }
    # state_config = state.TRIGGER_SCHEMA(state_config)
    # return await state.async_attach_trigger(
    #     hass, state_config, action, automation_info, platform_type="device"
    # )

    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: DOMAIN_EVENT,
            event_trigger.CONF_EVENT_DATA: {
                # CONF_DEVICE_ID: config[CONF_DEVICE_ID],
                CONF_TYPE: config[CONF_TYPE],
                CONF_ENTITY_ID: config[CONF_ENTITY_ID],
            },
        }
    )

    return await event_trigger.async_attach_trigger(
        hass, event_config, action, automation_info, platform_type="device"
    )
