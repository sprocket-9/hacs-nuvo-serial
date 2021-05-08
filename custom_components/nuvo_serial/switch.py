"""Support for interfacing with Nuvo multi-zone amplifier."""
from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

from nuvo_serial.const import ZONE_EQ_STATUS

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TYPE
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.typing import HomeAssistantType

from .const import CONTROL_EQ_LOUDCMP, DOMAIN, NUVO_OBJECT, ZONE
from .helpers import get_zones
from .nuvo_control import NuvoControl

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistantType,
    config_entry: ConfigEntry,
    async_add_entities: Callable[[Iterable[Entity], bool], None],
) -> None:
    """Set up the Switch entities associated with each Nuvo multi-zone amplifier zone."""

    model = config_entry.data[CONF_TYPE]
    nuvo = hass.data[DOMAIN][config_entry.entry_id][NUVO_OBJECT]
    zones = get_zones(config_entry)
    entities = []

    for zone_id, zone_name in zones.items():
        z_id = int(zone_id)
        entities.append(
            LoudnessCompensation(
                nuvo,
                model,
                config_entry.entry_id,
                z_id,
                ZONE,
                zone_name,
                CONTROL_EQ_LOUDCMP,
                ZONE_EQ_STATUS,
            )
        )

    async_add_entities(entities, False)


class LoudnessCompensation(NuvoControl, SwitchEntity):
    """Loudness Compensation control for Nuvo amplifier zone."""

    @property
    def is_on(self) -> bool:
        """Return True if entity is on."""
        return bool(self._control_value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self._nuvo.set_loudness_comp(self._nuvo_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self._nuvo.set_loudness_comp(self._nuvo_id, False)

    async def async_toggle(self, **kwargs: Any) -> None:
        """Toggle the entity."""
        await self._nuvo.set_loudness_comp(self._nuvo_id, not (self.is_on))

    async def _nuvo_get_control_value(self) -> None:
        """Get value."""
        await self._nuvo.zone_eq_status(self._nuvo_id)
