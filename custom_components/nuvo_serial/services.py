"""Services for the Nuvo multi-zone amplifier integration."""

from typing import Any, cast

from nuvo_serial.const import MODEL_GC
from nuvo_serial.grand_concerto_essentia_g import NuvoAsync
import voluptuous as vol

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID, CONF_TYPE, STATE_ON
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)

from .const import (
    CONTROL_NUVONET_SOURCE,
    DOMAIN,
    SERVICE_ATTR_DATETIME,
    SERVICE_ATTR_LINE1,
    SERVICE_ATTR_LINE2,
    SERVICE_ATTR_LINE3,
    SERVICE_ATTR_LINE4,
    SERVICE_CONFIGURE_TIME,
    SERVICE_SET_SOURCE_DISPLAY,
)

CONFIGURE_TIME_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): str,
        vol.Required(SERVICE_ATTR_DATETIME): cv.datetime,
    }
)

_SOURCE_DISPLAY_LINES = (
    SERVICE_ATTR_LINE1,
    SERVICE_ATTR_LINE2,
    SERVICE_ATTR_LINE3,
    SERVICE_ATTR_LINE4,
)


def _windows_1252(value: str) -> str:
    """Validate the value can be encoded as Windows-1252."""
    try:
        value.encode("windows-1252")
    except UnicodeEncodeError as err:
        raise vol.Invalid("must encode to windows-1252") from err
    return value


def _has_source_display_line(data: dict[str, Any]) -> dict[str, Any]:
    """Validate at least one source display line is supplied."""
    if not any(line in data for line in _SOURCE_DISPLAY_LINES):
        raise vol.Invalid("at least one display line must be supplied")
    return data


def _source_id_from_device_id(
    device_registry: dr.DeviceRegistry, device_id: str
) -> int:
    """Return the Nuvo source id associated with a source device."""
    if device := device_registry.async_get(device_id):
        for domain, identifier in device.identifiers:
            if domain != DOMAIN or not identifier.endswith("_source"):
                continue
            try:
                source = int(
                    identifier.removesuffix("_source").rsplit("_source_", 1)[1]
                )
            except IndexError, ValueError:
                continue
            if 1 <= source <= 6:
                return source
    raise HomeAssistantError(f"Nuvo source not found for device_id: {device_id}")


def _is_nuvonet_source_enabled(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, device_id: str, source: int
) -> bool:
    """Return if the source device has Nuvonet source enabled."""
    for entry in er.async_entries_for_device(entity_registry, device_id):
        if (
            entry.domain == SWITCH_DOMAIN
            and entry.unique_id.endswith(f"_source_{source}_{CONTROL_NUVONET_SOURCE}")
            and (state := hass.states.get(entry.entity_id))
        ):
            return state.state == STATE_ON
    return False


SET_SOURCE_DISPLAY_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): str,
            vol.Optional(SERVICE_ATTR_LINE1): vol.All(str, _windows_1252),
            vol.Optional(SERVICE_ATTR_LINE2): vol.All(str, _windows_1252),
            vol.Optional(SERVICE_ATTR_LINE3): vol.All(str, _windows_1252),
            vol.Optional(SERVICE_ATTR_LINE4): vol.All(str, _windows_1252),
        }
    ),
    _has_source_display_line,
)


def _get_nuvo_entry_from_device_id(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry, device_id: str
) -> tuple[ConfigEntry, NuvoAsync]:
    """Get the config entry and Nuvo connection associated with a device_id."""
    if (device := device_registry.async_get(device_id)) is None:
        raise ServiceValidationError(
            f"Nuvo device not found for device_id: {device_id}"
        )

    for config_entry_id in device.config_entries:
        if (entry := hass.config_entries.async_get_entry(config_entry_id)) is None:
            continue
        if entry.domain == DOMAIN and entry.state is ConfigEntryState.LOADED:
            return entry, cast(NuvoAsync, entry.runtime_data)

    raise ServiceValidationError(
        f"Nuvo connection not found for device_id: {device_id}"
    )


def _has_grand_concerto_entry(hass: HomeAssistant) -> bool:
    """Return if a Grand Concerto entry is currently loaded."""
    return any(
        entry.state is ConfigEntryState.LOADED and entry.data.get(CONF_TYPE) == MODEL_GC
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


@callback
def async_setup_services(hass: HomeAssistant, model: str) -> None:
    """Set up Nuvo services."""
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    async def configure_time(call: ServiceCall) -> None:
        """Service call to set the system time."""
        entry, connection = _get_nuvo_entry_from_device_id(
            hass, device_registry, call.data[ATTR_DEVICE_ID]
        )
        if entry.data[CONF_TYPE] != MODEL_GC:
            raise ServiceValidationError(
                "Configure time is only supported by Grand Concerto"
            )
        await connection.configure_time(call.data[SERVICE_ATTR_DATETIME])

    async def set_source_display(call: ServiceCall) -> None:
        """Service call to set source display lines."""
        device_id = call.data[ATTR_DEVICE_ID]
        _entry, connection = _get_nuvo_entry_from_device_id(
            hass, device_registry, device_id
        )
        source = _source_id_from_device_id(device_registry, device_id)
        if _is_nuvonet_source_enabled(hass, entity_registry, device_id, source):
            raise HomeAssistantError("Nuvonet sources manage their own display")
        for line_number, line in enumerate(_SOURCE_DISPLAY_LINES, start=1):
            if line in call.data:
                await connection.set_source_display_line(
                    source, line_number, call.data[line]
                )

    if model == MODEL_GC and not hass.services.has_service(
        DOMAIN, SERVICE_CONFIGURE_TIME
    ):
        hass.services.async_register(
            DOMAIN, SERVICE_CONFIGURE_TIME, configure_time, schema=CONFIGURE_TIME_SCHEMA
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SET_SOURCE_DISPLAY):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_SOURCE_DISPLAY,
            set_source_display,
            schema=SET_SOURCE_DISPLAY_SCHEMA,
        )


@callback
def async_unload_services(hass: HomeAssistant) -> None:
    """Unload Nuvo services."""
    if not hass.data.get(DOMAIN) and hass.services.has_service(
        DOMAIN, SERVICE_SET_SOURCE_DISPLAY
    ):
        hass.services.async_remove(DOMAIN, SERVICE_SET_SOURCE_DISPLAY)

    if not _has_grand_concerto_entry(hass) and hass.services.has_service(
        DOMAIN, SERVICE_CONFIGURE_TIME
    ):
        hass.services.async_remove(DOMAIN, SERVICE_CONFIGURE_TIME)
