"""Support for interfacing with Nuvo multi-zone amplifier."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
import logging
from typing import Any

from nuvo_serial.configuration import config
from nuvo_serial.const import ZONE_BUTTON, ZONE_CONFIGURATION, ZONE_STATUS
from nuvo_serial.grand_concerto_essentia_g import (
    NuvoAsync,
    ZoneButton,
    ZoneConfiguration,
    ZoneStatus,
)
import voluptuous as vol

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
)
from homeassistant.components.media_player.const import DOMAIN as MP_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_PORT,
    CONF_TYPE,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_VOLUME_STEP,
    DOMAIN,
    DOMAIN_EVENT,
    KEYPAD_BUTTON_TO_EVENT,
    NUVO_OBJECT,
    SERVICE_PARTY_OFF,
    SERVICE_PARTY_ON,
    SERVICE_RESTORE,
    SERVICE_SIMULATE_NEXT,
    SERVICE_SIMULATE_PLAY_PAUSE,
    SERVICE_SIMULATE_PREV,
    SERVICE_SNAPSHOT,
    SPEAKER_GROUP_CONTROLLER_MUTE_CHANGED,
    SPEAKER_GROUP_CONTROLLER_SOURCE_CHANGED,
    SPEAKER_GROUP_CONTROLLER_VOLUME_CHANGED,
    SPEAKER_GROUP_JOIN,
    SPEAKER_GROUP_MEMBER_LIST_JOINED,
    SPEAKER_GROUP_MEMBER_LIST_LEFT,
    ZONE,
)
from .helpers import get_sources, get_zones
from .speaker_group import SpeakerGroup

_LOGGER = logging.getLogger(__name__)

SERVICE_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTITY_ID): cv.entity_ids})


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Nuvo multi-zone amplifier platform."""
    model = config_entry.data[CONF_TYPE]

    nuvo = hass.data[DOMAIN][config_entry.entry_id][NUVO_OBJECT]
    port = config_entry.data[CONF_PORT]

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
                port,
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

    platform = entity_platform.async_get_current_platform()

    platform.async_register_entity_service(SERVICE_SNAPSHOT, SERVICE_SCHEMA, "snapshot")
    platform.async_register_entity_service(SERVICE_RESTORE, SERVICE_SCHEMA, "restore")
    platform.async_register_entity_service(SERVICE_PARTY_ON, SERVICE_SCHEMA, "party_on")
    platform.async_register_entity_service(
        SERVICE_PARTY_OFF, SERVICE_SCHEMA, "party_off"
    )
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

    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_has_entity_name = True

    _attr_supported_features = (
        MediaPlayerEntityFeature.GROUPING
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.SELECT_SOURCE
    )

    power_as_state = {
        True: STATE_ON,
        False: STATE_OFF,
    }
    state_as_power = {
        STATE_ON: True,
        STATE_OFF: False,
    }

    def __init__(
        self,
        nuvo: NuvoAsync,
        port: str,
        model: str,
        sources: list[Any],
        namespace: str,
        zone_id: int,
        zone_name: str,
        volume_step: int,
        max_volume: int,
        min_volume: int,
    ) -> None:
        """Initialize new zone."""
        self.nuvo = nuvo
        self._port = port
        self._model = model
        # dict source_id -> source name
        self._source_id_name = sources[0]
        # dict source name -> source_id
        self._source_name_id = sources[1]
        # ordered list of all source names
        self._source_names: list[str] = sources[2]

        self.zone_id = zone_id
        self._name = zone_name
        self._namespace = namespace
        self._volume_step = volume_step
        self._max_volume = max_volume
        self._min_volume = min_volume

        self._snapshot = None
        self._state: str | None = None
        self._volume: float | None = None
        self._source: str | None = None
        self._mute: bool | None = None
        self._speaker_group: SpeakerGroup = SpeakerGroup(self)
        self._events_removers: list[CALLBACK_TYPE] = []

    @property
    def should_poll(self) -> bool:
        """State updates are handled through subscription so turn polling off."""
        return False

    @property
    def available(self) -> bool:
        """Return is the media_player is available."""
        return bool(self._state)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this device."""

        identifiers = {(DOMAIN, self.unique_id)}
        manufacturer = "Nuvo"
        model = ZONE.capitalize()
        name = self._name.capitalize()

        return DeviceInfo(
            identifiers=identifiers,
            manufacturer=manufacturer,
            model=model,
            name=name,
            via_device=(DOMAIN, self._port),
        )

    @property
    def unique_id(self) -> str:
        """Return unique ID for this device."""
        return f"{self._namespace}_zone_{self.zone_id}_zone"

    @property
    def name(self) -> str | None:
        """Return the name of the zone."""
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the name of the control."""
        return {
            "zone_id": self.zone_id,
            "speaker_group_id": self._speaker_group.group_id,
            "speaker_group_controller": self._speaker_group.group_controller,
        }

    @property
    def state(self) -> str | None:
        """Return the state of the zone."""
        return self._state

    @property
    def volume_level(self) -> float | None:
        """Volume level of the media player (0..1)."""
        return self._volume

    @property
    def is_volume_muted(self) -> bool | None:
        """Boolean if volume is currently muted."""
        return self._mute

    @property
    def source(self) -> str | None:
        """Return the current input source of the device."""
        return self._source

    @property
    def source_list(self) -> list[str]:
        """List of available input sources."""
        return self._source_names

    @property
    def group_members(self) -> list[str]:
        """List of zone entity_ids in a nuvo group."""
        return self._speaker_group.group_members

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to register."""

        self.nuvo.add_subscriber(self._update_callback, ZONE_STATUS)
        self.nuvo.add_subscriber(self._update_callback, ZONE_CONFIGURATION)
        self.nuvo.add_subscriber(self._zone_button_callback, ZONE_BUTTON)

        self._events_removers.append(
            self.hass.bus.async_listen(
                SPEAKER_GROUP_JOIN, self._speaker_group.group_join_event_cb
            )
        )

        self._events_removers.append(
            self.hass.bus.async_listen(
                SPEAKER_GROUP_MEMBER_LIST_JOINED,
                self._speaker_group.group_member_list_joined_event_cb,
            )
        )

        self._events_removers.append(
            self.hass.bus.async_listen(
                SPEAKER_GROUP_MEMBER_LIST_LEFT,
                self._speaker_group.group_member_list_left_event_cb,
            )
        )

        self._events_removers.append(
            self.hass.bus.async_listen(
                SPEAKER_GROUP_CONTROLLER_MUTE_CHANGED,
                self._speaker_group.group_controller_mute_changed_event_cb,
            )
        )

        self._events_removers.append(
            self.hass.bus.async_listen(
                SPEAKER_GROUP_CONTROLLER_SOURCE_CHANGED,
                self._speaker_group.group_controller_source_changed_event_cb,
            )
        )

        self._events_removers.append(
            self.hass.bus.async_listen(
                SPEAKER_GROUP_CONTROLLER_VOLUME_CHANGED,
                self._speaker_group.group_controller_volume_changed_event_cb,
            )
        )

        await self.nuvo.zone_status(self.zone_id)
        await self.nuvo.zone_configuration(self.zone_id)

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity is removed to register.

        Remove Nuvo update callback.
        """
        self.nuvo.remove_subscriber(self._update_callback, ZONE_STATUS)
        self.nuvo.remove_subscriber(self._update_callback, ZONE_CONFIGURATION)
        self.nuvo.remove_subscriber(self._zone_button_callback, ZONE_BUTTON)
        self.nuvo = None

        for remove_event_listener in self._events_removers:
            remove_event_listener()

        del self._speaker_group

    async def _update_callback(self, message: ZoneConfiguration | ZoneStatus) -> None:
        """Update entity state callback.

        Nuvo lib calls this when it receives new messages.
        """
        event_name = message["event_name"]
        d_class = message["event"]
        if d_class.zone != self.zone_id:
            return
        _LOGGER.debug(
            "ZONE %d: Notified by nuvo that %s is available for update",
            self.zone_id,
            message,
        )

        if event_name == ZONE_CONFIGURATION:
            await self._process_zone_configuration(d_class)
            self.async_schedule_update_ha_state()
        elif event_name == ZONE_STATUS:
            state_change = self.process_zone_status(d_class)
            self.async_schedule_update_ha_state()
            # Allow this task to finish by continuing group state processing in a new
            # task
            if (
                self._speaker_group.zone_is_group_controller
                or self._speaker_group.zone_is_group_member
            ):
                if True in state_change.values():
                    self.hass.async_create_task(
                        self._speaker_group.propagate_group_state_changes(state_change)
                    )

    async def _zone_button_callback(self, message: ZoneButton) -> None:
        """Fire event when a zone keypad 'PLAYPAUSE', 'PREV' or 'NEXT' button is pressed."""

        if message["event"].zone != self.zone_id:
            return

        _LOGGER.debug("Firing ZoneButton event: %s", message)
        self.hass.bus.async_fire(
            DOMAIN_EVENT,
            {
                "type": KEYPAD_BUTTON_TO_EVENT[message["event"].button],
                ATTR_ENTITY_ID: self.entity_id,
            },
        )

    def process_zone_status(self, z_status: ZoneStatus) -> dict[str, bool]:
        """Update zone's power, volume and source state.

        A permitted source may not appear in the list of system-wide enabled sources.
        """

        state_change = {"power": False, "mute": False, "volume": False, "source": False}

        state_change["power"] = self._process_power(z_status.power)

        if self._state == STATE_OFF:
            self._mute = None
            self._volume = None
            self._source = None
            return state_change

        state_change["source"] = self._process_source(z_status.source)

        state_change["mute"] = self._process_mute(z_status.mute)

        if self._mute:
            return state_change

        state_change["volume"] = self._process_volume(z_status.volume)
        return state_change

    def _process_power(self, received_power_state: bool) -> bool:
        _power_changed = False
        if self._state is None or (
            _power_changed := self.state_as_power[self._state] != received_power_state
        ):
            self._state = self.power_as_state[received_power_state]

        return _power_changed

    def _process_mute(self, received_mute_state: bool) -> bool:
        """Process zone's mute status."""

        # Zone is ON here so received_mute_state will be a bool
        _mute_changed = False
        if self._mute is None or (_mute_changed := self._mute != received_mute_state):
            self._mute = received_mute_state

        if self._mute:
            self._volume = None

        return _mute_changed

    def _process_volume(self, received_nuvo_volume: int) -> bool:
        """Process zone's volume status.

        Ensure this is called only if mute status is false to guarantee
        received_nuvo_volume is an int
        """
        received_volume = self._nuvo_to_hass_vol(received_nuvo_volume)

        # Zone is ON here so received_volume will be an int
        _volume_changed = False
        if self._volume is None or (_volume_changed := self._volume != received_volume):
            self._volume = received_volume

        return _volume_changed

    def _process_source(self, received_source_id: int) -> bool:
        """Process zone's source selection."""

        # Zone is ON here so received_source_id will be an int, not None
        _received_source_name = self._source_id_name.get(received_source_id, None)

        _source_changed = False
        if self._source is None or (
            _source_changed := self._source != _received_source_name
        ):
            self._source = _received_source_name

        return _source_changed

    async def _process_zone_configuration(self, z_cfg: ZoneConfiguration) -> None:
        """Update zone's permitted sources.

        A permitted source may not appear in the list of system-wide enabled sources so
        need to filter these out.
        """
        self._source_names = list(
            filter(
                None,
                [
                    self._source_id_name.get(_id, None)
                    for _id in [int(src.split("SOURCE")[1]) for src in z_cfg.sources]
                ],
            )
        )

        # self._process_nuvo_group_status(z_cfg)

    # def _process_nuvo_group_status(self, z_cfg: ZoneConfiguration):
    #     """Process Nuvo group status."""
    #
    #     if not self.available:
    #         return
    #     if z_cfg.group and z_cfg.slave_to:
    #         # Don't process a slaved zone's group status let the master handle
    #         # things. Including slaved zones in group_members will result in
    #         # volume sync operations for master/slaves in a group being
    #         # repeated by the number of slaved zones in the group.
    #
    #         # This means a slaved zone's keypad should not be used to initiate
    #         # group/ungroup operations, it should be done from the master zone keypad.
    #         return
    #     if z_cfg.group and (self._nuvo_group_id and self._nuvo_group_id == z_cfg.group):
    #         # Group hasn't changed
    #         return
    #     if self._nuvo_group_id is None and z_cfg.group is None:
    #         return
    #     if self._nuvo_group_id is None and z_cfg.group == GROUP_NON_MEMBER:
    #         self._nuvo_group_id = z_cfg.group
    #         return
    #
    #     previous_group = self._nuvo_group_id
    #     self._nuvo_group_id = z_cfg.group
    #     notify_group = None
    #
    #     # Group Join
    #     if not previous_group and z_cfg.group:
    #         notify_group = z_cfg.group
    #
    #     # Group Leave
    #     elif previous_group and not z_cfg.group:
    #         self._clear_nuvo_group_info()
    #         notify_group = previous_group
    #
    #     self.async_schedule_update_ha_state()
    #     if notify_group:
    #         self.hass.bus.async_fire(f"{DOMAIN}_group_changed", {"group": notify_group})

    async def async_select_source(self, source: str) -> None:
        """Set input source."""
        if source not in self._source_name_id:
            return
        idx = self._source_name_id[source]
        await self.nuvo.set_source(self.zone_id, idx)

    async def async_turn_on(self) -> None:
        """Turn the media player on."""
        await self.nuvo.set_power(self.zone_id, True)

    async def async_turn_off(self) -> None:
        """Turn the media player off."""
        await self.nuvo.set_power(self.zone_id, False)

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute (true) or unmute (false) media player."""
        await self.nuvo.set_mute(self.zone_id, mute)

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level, range 0..1.

        This has to accept HA 0..1 levels of volume
        and do the conversion to Nuvo volume format.
        """
        nuvo_volume = self._hass_to_nuvo_vol(volume)
        await self.nuvo.set_volume(self.zone_id, nuvo_volume)

    async def async_volume_up(self) -> None:
        """Volume up the media player."""
        await self.nuvo.volume_up(self.zone_id)

    async def async_volume_down(self) -> None:
        """Volume down media player."""
        await self.nuvo.volume_down(self.zone_id)

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

    async def async_join_players(self, group_members: list[str]):
        """Join `group_members` as a player group with the current player."""

        await self._speaker_group.async_join_players(group_members)

    async def async_unjoin_player(self) -> None:
        """Remove this player from any group."""

        await self._speaker_group.async_unjoin_player()

    async def _nuvozone_id_to_hass_entity_id(self, zone_id) -> str | None:
        """Get the hass entity_id from the nuvo zone_id."""
        mp_states = self.hass.states.async_all(MP_DOMAIN)
        entity_id = None
        for ent_state in mp_states:
            z_id = ent_state.attributes.get("zone_id")
            if z_id and z_id == zone_id:
                entity_id = ent_state.entity_id
                break

        return entity_id

    async def snapshot(self) -> None:
        """Service handler to save zone's current state."""
        self._snapshot = await self.nuvo.zone_status(self.zone_id)

    async def restore(self) -> None:
        """Service handler to restore zone's saved state."""
        if self._snapshot:
            await self.nuvo.restore_zone(self._snapshot)

    async def party_on(self) -> None:
        """Service call to make this zone the party host."""
        await self.nuvo.set_party_host(self.zone_id, True)

    async def party_off(self) -> None:
        """Service call to release this zone from being the party host."""
        await self.nuvo.set_party_host(self.zone_id, False)

    async def simulate_play_pause_button(self) -> None:
        """Service call to simulate pressing keypad play/pause button."""
        await self.nuvo.zone_button_play_pause(self.zone_id)

    async def simulate_prev_button(self) -> None:
        """Service call to simulate pressing keypad prev button."""
        await self.nuvo.zone_button_prev(self.zone_id)

    async def simulate_next_button(self) -> None:
        """Service call to simulate pressing keypad next button."""
        await self.nuvo.zone_button_next(self.zone_id)
