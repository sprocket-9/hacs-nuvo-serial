"""Support for interfacing with Nuvo multi-zone amplifier."""
from __future__ import annotations

import logging

from nuvo_serial.grand_concerto_essentia_g import NuvoAsync
from nuvo_serial.message import ErrorResponse

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, NUVO_OBJECT

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Button entities associated with each Nuvo multi-zone amplifier zone."""

    nuvo = hass.data[DOMAIN][config_entry.entry_id][NUVO_OBJECT]
    port = config_entry.data[CONF_PORT]
    entities: list[Entity] = []

    entities.append(NuvoButton(nuvo, port, config_entry.entry_id, "all zones off"))

    async_add_entities(entities, False)


class NuvoButton(ButtonEntity):
    """Button to call the Nuvo All Off command."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, nuvo: NuvoAsync, port: str, namespace: str, name: str) -> None:
        """Initialize new button."""
        self._nuvo = nuvo
        self._port = port
        self._namespace = namespace
        self._name = name

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._port)},
        )

    @property
    def unique_id(self) -> str | None:
        """Return unique ID for this device."""
        return f"{self._namespace}_{'_'.join(self._name.split())}"

    @property
    def name(self) -> str | None:
        """Return the name of the button."""
        return self._name.capitalize()

    async def async_press(self) -> None:
        """Handle the button press."""
        response = await self._nuvo.all_off()
        if isinstance(response, ErrorResponse):
            raise HomeAssistantError(
                f"Nuvo system state preventing {self.name} - is paging mode active?"
            )
