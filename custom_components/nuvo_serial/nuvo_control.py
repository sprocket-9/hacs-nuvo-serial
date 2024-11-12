"""Support for interfacing with Nuvo multi-zone amplifier."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from nuvo_serial.grand_concerto_essentia_g import NuvoAsync

_LOGGER = logging.getLogger(__name__)


class NuvoControl:
    """Base class for Nuvo control."""

    _attr_has_entity_name = True

    def __init__(
        self,
        nuvo: NuvoAsync,
        model: str,
        namespace: str,
        nuvo_id: int,
        nuvo_entity_type: str,
        nuvo_entity_name: str,
        control_name: str,
        nuvo_config_key: str,
        nuvo_msg_class: str,
        port: str | None = None,
    ) -> None:
        """Init this entity."""
        self._nuvo = nuvo
        self._model = model
        self._namespace = namespace
        self._nuvo_id = nuvo_id
        self._nuvo_entity_type = nuvo_entity_type
        self._nuvo_entity_name = nuvo_entity_name
        self._control_name = control_name
        self._nuvo_config_key = nuvo_config_key
        self._nuvo_msg_class = nuvo_msg_class
        self._port = port

        self._control_value: float = 0
        self._available: bool = False

    @property
    def available(self) -> bool:
        """Return is the media_player is available."""
        return self._available

    @property
    def unique_id(self) -> str | None:
        """Return unique ID for this device."""
        return f"{self._namespace}_{self._nuvo_entity_type}_{self._nuvo_id}_{self._control_name}"  # noqa: E501

    @property
    def name(self) -> str | None:
        """Return the name of the control."""
        capitalized_control_name: str = ""
        parts = self._control_name.split("_")
        for part in parts:
            capitalized_control_name += part.capitalize()
            if part != parts[-1]:
                capitalized_control_name += "_"

        return f"{capitalized_control_name}"

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return the name of the control."""
        return {f"{self._nuvo_entity_type}_id": self._nuvo_id}

    @property
    def should_poll(self) -> bool:
        """State updates are handled through subscription so turn polling off."""
        return False

    # async def _nuvo_get_control_value(self) -> None:
    #     """Get value."""
    #     raise NotImplementedError

    # async def _nuvo_set_control_value(self, value: float) -> None:
    #     """Set new value."""
    #     raise NotImplementedError
