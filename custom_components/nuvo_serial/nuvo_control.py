"""Support for interfacing with Nuvo multi-zone amplifier."""
from __future__ import annotations

import logging
from typing import Any

from nuvo_serial.grand_concerto_essentia_g import NuvoAsync

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class NuvoControl:
    """Base class for Nuvo control."""

    def __init__(
        self,
        nuvo: NuvoAsync,
        model: str,
        namespace: str,
        nuvo_id: int,
        nuvo_entity_type: str,
        nuvo_entity_name: str,
        control_name: str,
        nuvo_msg_class: str,
    ):
        """Init this entity."""
        self._nuvo = nuvo
        self._model = model
        self._namespace = namespace
        self._nuvo_id = nuvo_id
        self._nuvo_entity_type = nuvo_entity_type
        self._nuvo_entity_name = nuvo_entity_name
        self._control_name = control_name
        self._nuvo_msg_class = nuvo_msg_class

        self._name = f"{self._nuvo_entity_name} {self._control_name.capitalize()}"
        self._unique_id = f"{self._namespace}_{self._nuvo_entity_type}_{self._nuvo_id}_{self._control_name}"  # noqa: E501
        self._control_value: float = 0
        self._available: bool = False

    @property
    def available(self) -> bool:
        """Return is the media_player is available."""
        return self._available

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device info for this device."""
        return {
            "identifiers": {(DOMAIN, self._namespace)},
            "name": f"{' '.join(self._model.split('_'))}",
            "manufacturer": "Nuvo",
            "model": self._model,
        }

    @property
    def unique_id(self) -> str:
        """Return unique ID for this device."""
        return self._unique_id

    @property
    def name(self) -> str:
        """Return the name of the control."""
        return self._name

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        """Return the name of the control."""
        return {f"{self._nuvo_entity_type}_id": self._nuvo_id}

    @property
    def should_poll(self) -> bool:
        """State updates are handled through subscription so turn polling off."""
        return False

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
            if self._control_name == "balance" and msg.balance_position == "L":
                self._control_value = -self._control_value
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

    async def _nuvo_set_control_value(self, value: float) -> None:
        """Set new value."""
        raise NotImplementedError
