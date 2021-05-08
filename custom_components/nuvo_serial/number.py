"""Support for interfacing with Nuvo multi-zone amplifier."""
from __future__ import annotations

import logging
from typing import Callable, Iterable

from nuvo_serial.configuration import config
from nuvo_serial.const import SOURCE_CONFIGURATION, ZONE_EQ_STATUS

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TYPE
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.typing import HomeAssistantType

from .const import (
    CONTROL_EQ_BALANCE,
    CONTROL_EQ_BASS,
    CONTROL_EQ_TREBLE,
    CONTROL_SOURCE_GAIN,
    DOMAIN,
    NUVO_OBJECT,
    SOURCE,
    ZONE,
)
from .helpers import get_sources, get_zones
from .nuvo_control import NuvoControl

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistantType,
    config_entry: ConfigEntry,
    async_add_entities: Callable[[Iterable[Entity], bool], None],
) -> None:
    """Set up the Number entities associated with each Nuvo multi-zone amplifier zone."""

    model = config_entry.data[CONF_TYPE]
    nuvo = hass.data[DOMAIN][config_entry.entry_id][NUVO_OBJECT]
    zones = get_zones(config_entry)
    sources = get_sources(config_entry)[0]
    entities: list[EQ] = []

    for zone_id, zone_name in zones.items():
        z_id = int(zone_id)
        entities.append(
            Bass(
                nuvo,
                model,
                config_entry.entry_id,
                z_id,
                ZONE,
                zone_name,
                CONTROL_EQ_BASS,
                ZONE_EQ_STATUS,
            )
        )
        entities.append(
            Treble(
                nuvo,
                model,
                config_entry.entry_id,
                z_id,
                ZONE,
                zone_name,
                CONTROL_EQ_TREBLE,
                ZONE_EQ_STATUS,
            )
        )
        entities.append(
            Balance(
                nuvo,
                model,
                config_entry.entry_id,
                z_id,
                ZONE,
                zone_name,
                CONTROL_EQ_BALANCE,
                ZONE_EQ_STATUS,
            )
        )

    for source_id, source_name in sources.items():
        s_id = int(source_id)
        entities.append(
            GainControl(
                nuvo,
                model,
                config_entry.entry_id,
                s_id,
                SOURCE,
                source_name,
                CONTROL_SOURCE_GAIN,
                SOURCE_CONFIGURATION,
            )
        )

    async_add_entities(entities, False)


class NuvoNumberControl(NumberEntity):
    """Nuvo Number based control."""

    @property
    def min_value(self) -> float:
        """Return the minimum value."""
        return float(config[self._model][self._control_name]["min"])

    @property
    def max_value(self) -> float:
        """Return the maximum value."""
        return float(config[self._model][self._control_name]["max"])

    @property
    def step(self) -> float:
        """Return the increment/decrement step."""
        return float(config[self._model][self._control_name]["step"])

    @property
    def value(self) -> float:
        """Return the entity value to represent the entity state."""
        return self._control_value

    async def async_set_value(self, value: float) -> None:
        """Set new value."""

        return await self._nuvo_set_control_value(value)

    async def _nuvo_get_control_value(self) -> None:
        """Get value."""
        raise NotImplementedError

    async def _nuvo_set_control_value(self, value: float) -> None:
        """Set new value."""
        raise NotImplementedError


class EQ(NuvoControl, NuvoNumberControl):
    """Nuvo EQ based control."""

    async def _nuvo_get_control_value(self) -> None:
        """Get value."""
        await self._nuvo.zone_eq_status(self._nuvo_id)


class Bass(EQ):
    """Bass control for Nuvo amplifier zone."""

    async def _nuvo_set_control_value(self, value: float) -> None:
        """Set new value."""
        await self._nuvo.set_bass(self._nuvo_id, int(value))


class Treble(EQ):
    """Treble control for Nuvo amplifier zone."""

    async def _nuvo_set_control_value(self, value: float) -> None:
        """Set new value."""
        await self._nuvo.set_treble(self._nuvo_id, int(value))


class Balance(EQ):
    """Balance control for Nuvo amplifier zone.

    In order to control the balance from one frontend UI slider control, represent R
    balance with positive values and L balance with negative values.
    """

    @property
    def min_value(self) -> float:
        """Return the minimum value."""
        max = float(config[self._model][CONTROL_EQ_BALANCE]["max"])
        return -max

    async def _nuvo_set_control_value(self, value: float) -> None:
        """Set new value."""
        balance_position = "C"

        if value < 0:
            balance_position = "L"
            value = -value
        elif value > 0:
            balance_position = "R"

        await self._nuvo.set_balance(self._nuvo_id, balance_position, int(value))


class GainControl(NuvoControl, NuvoNumberControl):
    """Gain control for Nuvo amplifier source."""

    async def _nuvo_get_control_value(self) -> None:
        """Get value."""
        await self._nuvo.source_configuration(self._nuvo_id)

    async def _nuvo_set_control_value(self, value: float) -> None:
        """Set new value."""
        await self._nuvo.set_source_gain(self._nuvo_id, int(value))
