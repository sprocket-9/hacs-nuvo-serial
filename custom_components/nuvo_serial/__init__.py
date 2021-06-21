"""The Nuvo multi-zone amplifier integration."""
import asyncio
import logging

from nuvo_serial import get_nuvo_async

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PORT, CONF_TYPE
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.typing import ConfigType, HomeAssistantType, ServiceCallType

from .const import (
    COMMAND_RESPONSE_TIMEOUT,
    DOMAIN,
    NUVO_OBJECT,
    SERVICE_ALL_OFF,
    SERVICE_PAGE_OFF,
    SERVICE_PAGE_ON,
    UNDO_UPDATE_LISTENER,
)

PLATFORMS = ["media_player", "number", "switch"]

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistantType, config: ConfigType) -> bool:
    """Set up the Nuvo multi-zone amplifier component."""
    return True


async def async_setup_entry(hass: HomeAssistantType, entry: ConfigEntry) -> bool:
    """Set up Nuvo multi-zone amplifier from a config entry."""

    port = entry.options.get(CONF_PORT, entry.data[CONF_PORT])
    model = entry.data[CONF_TYPE]

    try:
        nuvo = await get_nuvo_async(port, model, timeout=COMMAND_RESPONSE_TIMEOUT)
    except Exception as err:
        _LOGGER.error("Error connecting to Nuvo controller at %s", port)
        raise ConfigEntryNotReady from err

    undo_listener = entry.add_update_listener(_update_listener)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        NUVO_OBJECT: nuvo,
        UNDO_UPDATE_LISTENER: undo_listener,
    }

    for component in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, component)
        )

    async def page_on(call: ServiceCallType) -> None:
        """Service call to turn paging on."""
        await nuvo.set_page(True)

    async def page_off(call: ServiceCallType) -> None:
        """Service call to turn paging off."""
        await nuvo.set_page(False)

    async def all_off(call: ServiceCallType) -> None:
        """Service call to turn all zones off."""
        await nuvo.all_off()

    hass.services.async_register(DOMAIN, SERVICE_PAGE_ON, page_on, schema=None)

    hass.services.async_register(DOMAIN, SERVICE_PAGE_OFF, page_off, schema=None)

    hass.services.async_register(DOMAIN, SERVICE_ALL_OFF, all_off, schema=None)

    return True


async def async_unload_entry(hass: HomeAssistantType, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )

    # Disconnect and free the serial port
    await hass.data[DOMAIN][entry.entry_id][NUVO_OBJECT].disconnect()

    if unload_ok:
        hass.data[DOMAIN][entry.entry_id][NUVO_OBJECT] = None
        hass.data[DOMAIN][entry.entry_id][UNDO_UPDATE_LISTENER]()
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def _update_listener(hass: HomeAssistantType, entry: ConfigEntry) -> None:
    """Handle options update."""

    await hass.config_entries.async_reload(entry.entry_id)
