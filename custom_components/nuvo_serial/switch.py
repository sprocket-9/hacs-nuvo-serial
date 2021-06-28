"""Support for interfacing with Nuvo multi-zone amplifier."""
from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

from nuvo_serial.const import ZONE_EQ_STATUS, ZONE_VOLUME_CONFIGURATION

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TYPE
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.typing import HomeAssistantType

from .const import CONTROL_EQ_LOUDCMP, CONTROL_VOLUME_RESET, DOMAIN, NUVO_OBJECT, ZONE
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
    entities: list[Entity] = []

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
                CONTROL_EQ_LOUDCMP,
                ZONE_EQ_STATUS,
            )
        )

        entities.append(
            VolumeReset(
                nuvo,
                model,
                config_entry.entry_id,
                z_id,
                ZONE,
                zone_name,
                CONTROL_VOLUME_RESET,
                CONTROL_VOLUME_RESET,
                ZONE_VOLUME_CONFIGURATION,
            )
        )

    async_add_entities(entities, False)


class NuvoSwitchControl(NuvoControl, SwitchEntity):
    """Nuvo Switch based control."""

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device info for this device."""
        return {
            "identifiers": {(DOMAIN, self._namespace)},
            "name": f"{' '.join(self._model.split('_'))}",
            "manufacturer": "Nuvo",
            "model": self._model,
        }

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to register.

        Subscribe callback to handle updates from the Nuvo.
        Request initial entity state, letting the update callback handle setting it.
        """
        self._nuvo.add_subscriber(self._update_callback, self._nuvo_msg_class)
        await self._nuvo_get_control_value()

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity is removed to register.

        Remove Nuvo update callback.
        """
        self._nuvo.remove_subscriber(self._update_callback, self._nuvo_msg_class)
        self._nuvo = None

    async def _update_callback(self, message: dict[str, Any]) -> None:
        """Update entity state callback.

        Nuvo lib calls this when it receives new messages.
        """
        try:
            msg = message["event"]
            originating_id = getattr(msg, self._nuvo_entity_type)
            if originating_id != self._nuvo_id:
                return
            self._control_value = float(getattr(msg, self._control_name))
            self._available = True
        except (KeyError, AttributeError):
            _LOGGER.debug(
                "%s %d %s: invalid %s message received",
                self._nuvo_entity_type,
                self._nuvo_id,
                self.entity_id,
                self._control_name,
            )
            return
        else:
            self.async_schedule_update_ha_state()

    async def _nuvo_get_control_value(self) -> None:
        """Get value."""
        raise NotImplementedError


class LoudnessCompensation(NuvoSwitchControl):
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


class VolumeReset(NuvoSwitchControl):
    """Volume Reset control for Nuvo amplifier zone."""

    @property
    def is_on(self) -> bool:
        """Return True if entity is on."""
        return bool(self._control_value)

    @property
    def name(self) -> str:
        """Return the name of the control."""
        return f"{self._nuvo_entity_name} Volume Reset"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self._nuvo.zone_volume_reset(self._nuvo_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self._nuvo.zone_volume_reset(self._nuvo_id, False)

    async def async_toggle(self, **kwargs: Any) -> None:
        """Toggle the entity."""
        await self._nuvo.zone_volume_reset(self._nuvo_id, not (self.is_on))

    async def _nuvo_get_control_value(self) -> None:
        """Get value."""
        await self._nuvo.zone_volume_configuration(self._nuvo_id)
