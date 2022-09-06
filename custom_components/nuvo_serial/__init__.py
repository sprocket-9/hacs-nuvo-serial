"""The Nuvo multi-zone amplifier integration."""
import logging

from nuvo_serial import get_nuvo_async
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID, CONF_PORT, CONF_TYPE, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import (
    COMMAND_RESPONSE_TIMEOUT,
    DOMAIN,
    NUVO_OBJECT,
    SERVICE_ALL_OFF,
    SERVICE_ATTR_DATETIME,
    SERVICE_CONFIGURE_TIME,
    SERVICE_PAGE_OFF,
    SERVICE_PAGE_ON,
)

PLATFORMS = [Platform.MEDIA_PLAYER, Platform.NUMBER, Platform.SWITCH]

CONFIGURE_TIME_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): str,
        vol.Required(SERVICE_ATTR_DATETIME): cv.datetime,
    }
)
DEVICE_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): str})

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Nuvo multi-zone amplifier component."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Nuvo multi-zone amplifier from a config entry."""

    port = entry.options.get(CONF_PORT, entry.data[CONF_PORT])
    model = entry.data[CONF_TYPE]

    try:
        nuvo = await get_nuvo_async(port, model, timeout=COMMAND_RESPONSE_TIMEOUT)
    except Exception as err:
        _LOGGER.error("Error connecting to Nuvo controller at %s", port)
        raise ConfigEntryNotReady from err

    entry.async_on_unload(entry.add_update_listener(_update_listener))

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {NUVO_OBJECT: nuvo}

    version = await nuvo.get_version()

    device_registry = dr.async_get(hass)

    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, port)},
        manufacturer="Nuvo",
        model=f"{' '.join(version.model.split('_'))} {version.product_number}",
        name=f"{' '.join(model.split('_'))}",
        sw_version=version.firmware_version,
        hw_version=version.hardware_version,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def page_on(call: ServiceCall) -> None:
        """Service call to turn paging on."""
        await nuvo.set_page(True)

    async def page_off(call: ServiceCall) -> None:
        """Service call to turn paging off."""
        await nuvo.set_page(False)

    async def all_off(call: ServiceCall) -> None:
        """Service call to turn all zones off."""
        await nuvo.all_off()

    async def configure_time(call: ServiceCall) -> None:
        """Service call to set the system timeturn all zones off."""
        await nuvo.configure_time(call.data[SERVICE_ATTR_DATETIME])

    hass.services.async_register(DOMAIN, SERVICE_PAGE_ON, page_on, schema=DEVICE_SCHEMA)

    hass.services.async_register(
        DOMAIN, SERVICE_PAGE_OFF, page_off, schema=DEVICE_SCHEMA
    )

    hass.services.async_register(DOMAIN, SERVICE_ALL_OFF, all_off, schema=None)

    hass.services.async_register(
        DOMAIN, SERVICE_CONFIGURE_TIME, configure_time, schema=CONFIGURE_TIME_SCHEMA
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Disconnect and free the serial port
    await hass.data[DOMAIN][entry.entry_id][NUVO_OBJECT].disconnect()

    if unload_ok:
        hass.data[DOMAIN][entry.entry_id][NUVO_OBJECT] = None
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def _update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""

    await hass.config_entries.async_reload(entry.entry_id)
