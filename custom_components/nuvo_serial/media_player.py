"""Support for interfacing with Nuvo multi-zone amplifier."""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
import logging
from typing import Any, Callable, Iterable

from nuvo_serial.configuration import config
from nuvo_serial.const import ZONE_BUTTON, ZONE_CONFIGURATION, ZONE_STATUS
from nuvo_serial.grand_concerto_essentia_g import (
    NuvoAsync,
    ZoneButton,
    ZoneConfiguration,
    ZoneStatus,
)
import voluptuous as vol

from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.const import (
    SUPPORT_SELECT_SOURCE,
    SUPPORT_TURN_OFF,
    SUPPORT_TURN_ON,
    SUPPORT_VOLUME_MUTE,
    SUPPORT_VOLUME_SET,
    SUPPORT_VOLUME_STEP,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, CONF_TYPE, STATE_OFF, STATE_ON
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.typing import HomeAssistantType

from .const import (
    CONF_VOLUME_STEP,
    DOMAIN,
    DOMAIN_EVENT,
    KEYPAD_BUTTON_TO_EVENT,
    NUVO_OBJECT,
    SERVICE_RESTORE,
    SERVICE_SIMULATE_NEXT,
    SERVICE_SIMULATE_PLAY_PAUSE,
    SERVICE_SIMULATE_PREV,
    SERVICE_SNAPSHOT,
)
from .helpers import get_sources, get_zones

_LOGGER = logging.getLogger(__name__)

SUPPORT_NUVO_SERIAL = (
    SUPPORT_VOLUME_MUTE
    | SUPPORT_VOLUME_SET
    | SUPPORT_VOLUME_STEP
    | SUPPORT_TURN_ON
    | SUPPORT_TURN_OFF
    | SUPPORT_SELECT_SOURCE
)


async def async_setup_entry(
    hass: HomeAssistantType,
    config_entry: ConfigEntry,
    async_add_entities: Callable[[Iterable[Entity], bool], None],
) -> None:
    """Set up the Nuvo multi-zone amplifier platform."""
    model = config_entry.data[CONF_TYPE]

    nuvo = hass.data[DOMAIN][config_entry.entry_id][NUVO_OBJECT]

    sources = get_sources(config_entry)
    zones = get_zones(config_entry)
    volume_step = config_entry.data.get(CONF_VOLUME_STEP, 1)
    max_volume = config[model]["volume"]["max"]
    min_volume = config[model]["volume"]["min"]
    entities = []

    for zone_id, zone_name in zones.items():
        z_id = int(zone_id)
        entities.append(
            NuvoZone(
                nuvo,
                model,
                sources,
                config_entry.entry_id,
                z_id,
                zone_name,
                volume_step,
                max_volume,
                min_volume,
            )
        )

    async_add_entities(entities, False)

    platform = entity_platform.current_platform.get()

    SERVICE_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTITY_ID): cv.entity_ids})

    platform.async_register_entity_service(SERVICE_SNAPSHOT, SERVICE_SCHEMA, "snapshot")  # type: ignore
    platform.async_register_entity_service(SERVICE_RESTORE, SERVICE_SCHEMA, "restore")  # type: ignore
    platform.async_register_entity_service(
        SERVICE_SIMULATE_PLAY_PAUSE, SERVICE_SCHEMA, "simulate_play_pause_button"
    )
    platform.async_register_entity_service(
        SERVICE_SIMULATE_PREV, SERVICE_SCHEMA, "simulate_prev_button"
    )
    platform.async_register_entity_service(
        SERVICE_SIMULATE_NEXT, SERVICE_SCHEMA, "simulate_next_button"
    )


class NuvoZone(MediaPlayerEntity):
    """Representation of a Nuvo amplifier zone."""

    def __init__(
        self,
        nuvo: NuvoAsync,
        model: str,
        sources: list[Any],
        namespace: str,
        zone_id: int,
        zone_name: str,
        volume_step: int,
        max_volume: int,
        min_volume: int,
    ):
        """Initialize new zone."""
        self._nuvo = nuvo
        self._model = model
        # dict source_id -> source name
        self._source_id_name = sources[0]
        # dict source name -> source_id
        self._source_name_id = sources[1]
        # ordered list of all source names
        self._source_names: list[str] = sources[2]

        self._zone_id = zone_id
        self._name = zone_name
        self._namespace = namespace
        self._unique_id = f"{self._namespace}_zone_{self._zone_id}_zone"
        self._volume_step = volume_step
        self._max_volume = max_volume
        self._min_volume = min_volume

        self._snapshot = None
        self._state: str = ""
        self._volume: float | None
        self._source: str
        self._mute: bool

    @property
    def should_poll(self) -> bool:
        """State updates are handled through subscription so turn polling off."""
        return False

    @property
    def available(self) -> bool:
        """Return is the media_player is available."""
        return bool(self._state)

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
        """Return the name of the zone."""
        return self._name

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        """Return the name of the control."""
        return {"zone_id": self._zone_id}

    @property
    def state(self) -> str:
        """Return the state of the zone."""
        return self._state

    @property
    def volume_level(self) -> float:
        """Volume level of the media player (0..1)."""
        return self._volume

    @property
    def is_volume_muted(self) -> bool:
        """Boolean if volume is currently muted."""
        return self._mute

    @property
    def supported_features(self) -> int:
        """Return flag of media commands that are supported."""
        return SUPPORT_NUVO_SERIAL

    @property
    def source(self) -> str:
        """Return the current input source of the device."""
        return self._source

    @property
    def source_list(self) -> list[str]:
        """List of available input sources."""
        return self._source_names

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to register.

        Subscribe callback to handle updates from the Nuvo.
        Request initial entity state, letting the update callback handle setting it.
        """
        self._nuvo.add_subscriber(self._update_callback, ZONE_STATUS)
        self._nuvo.add_subscriber(self._update_callback, ZONE_CONFIGURATION)
        self._nuvo.add_subscriber(self._zone_button_callback, ZONE_BUTTON)
        await self._nuvo.zone_status(self._zone_id)
        await self._nuvo.zone_configuration(self._zone_id)

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity is removed to register.

        Remove Nuvo update callback.
        """
        self._nuvo.remove_subscriber(self._update_callback, ZONE_STATUS)
        self._nuvo.remove_subscriber(self._update_callback, ZONE_CONFIGURATION)
        self._nuvo.remove_subscriber(self._zone_button_callback, ZONE_BUTTON)
        self._nuvo = None

    async def _update_callback(self, message: ZoneConfiguration | ZoneStatus) -> None:
        """Update entity state callback.

        Nuvo lib calls this when it receives new messages.
        """
        event_name = message["event_name"]
        d_class = message["event"]
        if d_class.zone != self._zone_id:
            return
        _LOGGER.debug(
            "ZONE %d: Notified by nuvo that %s is available for update",
            self._zone_id,
            message,
        )

        if event_name == ZONE_CONFIGURATION:
            self._process_zone_configuration(d_class)
        elif event_name == ZONE_STATUS:
            self._process_zone_status(d_class)
        else:
            return

        self.async_schedule_update_ha_state()

    async def _zone_button_callback(self, message: ZoneButton) -> None:
        """Fire event when a zone keypad 'PLAYPAUSE', 'PREV' or 'NEXT' button is pressed."""

        if message["event"].zone != self._zone_id:
            return

        _LOGGER.debug("Firing ZoneButton event: %s", message)
        self.hass.bus.async_fire(
            DOMAIN_EVENT,
            {
                "type": KEYPAD_BUTTON_TO_EVENT[message["event"].button],
                ATTR_ENTITY_ID: self.entity_id,
            },
        )

    def _process_zone_status(self, z_status: ZoneStatus) -> None:
        """Update zone's power, volume and source state.

        A permitted source may not appear in the list of system-wide enabled sources.
        """
        if not z_status.power:
            self._state = STATE_OFF
            return

        self._state = STATE_ON
        self._mute = z_status.mute

        if self._mute:
            self._volume = None
        else:
            self._volume = self._nuvo_to_hass_vol(z_status.volume)

        self._source = self._source_id_name.get(z_status.source, None)

    def _process_zone_configuration(self, z_cfg: ZoneConfiguration) -> None:
        """Update zone's permitted sources.

        A permitted source may not appear in the list of system-wide enabled sources so
        need to filter these out.
        """
        self._source_names = list(
            filter(
                None,
                [
                    self._source_id_name.get(id, None)
                    for id in [int(src.split("SOURCE")[1]) for src in z_cfg.sources]
                ],
            )
        )

    async def async_select_source(self, source: str) -> None:
        """Set input source."""
        if source not in self._source_name_id:
            return
        idx = self._source_name_id[source]
        await self._nuvo.set_source(self._zone_id, idx)

    async def async_turn_on(self) -> None:
        """Turn the media player on."""
        await self._nuvo.set_power(self._zone_id, True)

    async def async_turn_off(self) -> None:
        """Turn the media player off."""
        await self._nuvo.set_power(self._zone_id, False)

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute (true) or unmute (false) media player."""
        await self._nuvo.set_mute(self._zone_id, mute)

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level, range 0..1.

        This has to accept HA 0..1 levels of volume
        and do the conversion to Nuvo volume format.
        """

        nuvo_volume = self._hass_to_nuvo_vol(volume)
        await self._nuvo.set_volume(self._zone_id, nuvo_volume)

    async def async_volume_up(self) -> None:
        """Volume up the media player."""

        # If HA starts when a zone is muted _volume will be None as volume level is only
        # returned form a unmuted zone.
        if self._volume is None:
            return

        await self._nuvo.set_volume(
            self._zone_id,
            max(
                self._hass_to_nuvo_vol(self._volume) - self._volume_step,
                self._max_volume,
            ),
        )

    async def async_volume_down(self) -> None:
        """Volume down media player."""

        # If HA starts when a zone is muted _volume will be None as volume level is only
        # returned form a unmuted zone.
        if self._volume is None:
            return

        await self._nuvo.set_volume(
            self._zone_id,
            min(
                self._hass_to_nuvo_vol(self._volume) + self._volume_step,
                self._min_volume,
            ),
        )

    def _nuvo_to_hass_vol(self, volume: int) -> float:
        """Convert from nuvo to hass volume."""
        return 1 - (volume / self._min_volume)

    def _hass_to_nuvo_vol(self, volume: float) -> int:
        """Convert from hass to nuvo volume."""
        return int(
            Decimal(self._min_volume - (volume * self._min_volume)).to_integral_exact(
                rounding=ROUND_HALF_EVEN
            )
        )

    async def snapshot(self) -> None:
        """Service handler to save zone's current state."""
        self._snapshot = await self._nuvo.zone_status(self._zone_id)

    async def restore(self) -> None:
        """Service handler to restore zone's saved state."""
        if self._snapshot:
            await self._nuvo.restore_zone(self._snapshot)

    async def simulate_play_pause_button(self) -> None:
        """Service call to simulate pressing keypad play/pause button."""
        await self._nuvo.zone_button_play_pause(self._zone_id)

    async def simulate_prev_button(self) -> None:
        """Service call to simulate pressing keypad prev button."""
        await self._nuvo.zone_button_prev(self._zone_id)

    async def simulate_next_button(self) -> None:
        """Service call to simulate pressing keypad next button."""
        await self._nuvo.zone_button_next(self._zone_id)
