"""The Nuvo multi-zone amplifier integration."""

import logging

from nuvo_serial import get_nuvo_async

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PORT, CONF_TYPE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import COMMAND_RESPONSE_TIMEOUT, DOMAIN, NUVO_OBJECT
from .services import async_setup_services, async_unload_services

PLATFORMS = [Platform.BUTTON, Platform.MEDIA_PLAYER, Platform.NUMBER, Platform.SWITCH]

_LOGGER = logging.getLogger(__name__)


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

    entry.runtime_data = nuvo
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

    async_setup_services(hass, model)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Disconnect and free the serial port
    await hass.data[DOMAIN][entry.entry_id][NUVO_OBJECT].disconnect()

    if unload_ok:
        hass.data[DOMAIN][entry.entry_id][NUVO_OBJECT] = None
        hass.data[DOMAIN].pop(entry.entry_id)
        async_unload_services(hass)

    return unload_ok


async def _update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""

    await hass.config_entries.async_reload(entry.entry_id)
