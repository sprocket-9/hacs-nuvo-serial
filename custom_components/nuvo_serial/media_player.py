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
    DOMAIN as MP_DOMAIN,
    SUPPORT_GROUPING,
    SUPPORT_NEXT_TRACK,
    SUPPORT_PAUSE,
    SUPPORT_PLAY,
    SUPPORT_PREVIOUS_TRACK,
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
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.typing import HomeAssistantType

from .const import (
    CONF_VOLUME_STEP,
    DOMAIN,
    DOMAIN_EVENT,
    GROUP_MEMBER,
    GROUP_NON_MEMBER,
    KEYPAD_BUTTON_TO_EVENT,
    NUVO_OBJECT,
    SERVICE_PARTY_OFF,
    SERVICE_PARTY_ON,
    SERVICE_RESTORE,
    SERVICE_SNAPSHOT,
)
from .helpers import get_sources, get_zones

_LOGGER = logging.getLogger(__name__)

SUPPORT_NUVO_SERIAL = (
    SUPPORT_GROUPING
    | SUPPORT_NEXT_TRACK
    | SUPPORT_PAUSE
    | SUPPORT_PLAY
    | SUPPORT_PREVIOUS_TRACK
    | SUPPORT_VOLUME_MUTE
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

    platform = entity_platform.async_get_current_platform()

    SERVICE_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTITY_ID): cv.entity_ids})

    platform.async_register_entity_service(SERVICE_SNAPSHOT, SERVICE_SCHEMA, "snapshot")
    platform.async_register_entity_service(SERVICE_RESTORE, SERVICE_SCHEMA, "restore")
    platform.async_register_entity_service(SERVICE_PARTY_ON, SERVICE_SCHEMA, "party_on")
    platform.async_register_entity_service(
        SERVICE_PARTY_OFF, SERVICE_SCHEMA, "party_off"
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
        self._nuvo_group_members: list[str] = []
        self._nuvo_group_members_zone_ids: set[int] = set()
        self._nuvo_group_id: int | None = None
        self._nuvo_group_controller: str = ""

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
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the name of the control."""
        return {
            "zone_id": self._zone_id,
            "nuvo_group_id": self._nuvo_group_id,
            "nuvo_group_controller": self._nuvo_group_controller,
        }

    @property
    def state(self) -> str:
        """Return the state of the zone."""
        return self._state

    @property
    def volume_level(self) -> float | None:
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

    @property
    def group_members(self) -> list[str]:
        """List of zone entity_ids in a nuvo group."""
        return self._nuvo_group_members

    @property
    def group_controller(self) -> str:
        """Return entity_id of the zone that is the group controller.

        This zone will control volume sync for the group.
        """
        return self._nuvo_group_controller

    @property
    def group_id(self) -> int | None:
        """Return Nuvo group number."""
        return self._nuvo_group_id

    @property
    def zone_is_group_controller(self) -> bool:
        """Is this zone the group controller."""
        return self._nuvo_group_controller == self.entity_id

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to register.

        Subscribe callback to handle updates from the Nuvo.
        Request initial entity state, letting the update callback handle setting it.
        """

        self._nuvo.add_subscriber(self._update_callback, ZONE_STATUS)
        self._nuvo.add_subscriber(self._update_callback, ZONE_CONFIGURATION)
        self._nuvo.add_subscriber(self._zone_button_callback, ZONE_BUTTON)

        self.hass.bus.async_listen(
            f"{DOMAIN}_group_changed", self._group_membership_changed_cb
        )

        self.hass.bus.async_listen(
            f"{DOMAIN}_group_controller_changed", self._group_controller_changed_cb
        )

        self.hass.bus.async_listen(
            f"{DOMAIN}_join_group", self._nuvo_join_group_event_cb
        )

        await self._get_group_members()
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
            await self._process_zone_configuration(d_class)
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

    async def _process_zone_configuration(self, z_cfg: ZoneConfiguration) -> None:
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

        # Process zone's group status
        if not self.available:
            return
        if z_cfg.group and z_cfg.slave_to:
            """
            # Don't process a slaved zone's group status let the master handle
            # things. Including slaved zones in group_members will result in
            # volume sync operations for master/slaves in a group being
            # repeated by the number of slaved zones in the group.

            # This means a slaved zone's keypad should not be used to initiate
            # group/ungroup operations, it should be done from the master zone keypad.
            """
            return
        if z_cfg.group and (self._nuvo_group_id and self._nuvo_group_id == z_cfg.group):
            # Group hasn't changed
            return
        if self._nuvo_group_id is None and z_cfg.group is None:
            return
        if self._nuvo_group_id is None and z_cfg.group == GROUP_NON_MEMBER:
            self._nuvo_group_id = z_cfg.group
            return

        previous_group = self._nuvo_group_id
        self._nuvo_group_id = z_cfg.group
        notify_group = None

        # Group Join
        if not previous_group and z_cfg.group:
            notify_group = z_cfg.group

        # Group Leave
        elif previous_group and not z_cfg.group:
            self._nuvo_group_controller = ""
            self._nuvo_group_members = []
            self._nuvo_group_members_zone_ids = set()
            notify_group = previous_group

        self.async_schedule_update_ha_state()
        self.hass.bus.async_fire(f"{DOMAIN}_group_changed", {"group": notify_group})

    async def async_select_source(self, source: str) -> None:
        """Set input source."""
        if source not in self._source_name_id:
            return
        idx = self._source_name_id[source]
        await self._nuvo.set_source(self._zone_id, idx)

    async def async_turn_on(self) -> None:
        """Turn the media player on."""
        if self.zone_is_group_controller:
            for zone_id in self._nuvo_group_members_zone_ids:
                await self._nuvo.set_power(zone_id, True)
        else:
            await self._nuvo.set_power(self._zone_id, True)

    async def async_turn_off(self) -> None:
        """Turn the media player off."""
        if self.zone_is_group_controller:
            for zone_id in self._nuvo_group_members_zone_ids:
                await self._nuvo.set_power(zone_id, False)
        else:
            await self._nuvo.set_power(self._zone_id, False)

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute (true) or unmute (false) media player."""
        if self.zone_is_group_controller:
            for zone_id in self._nuvo_group_members_zone_ids:
                await self._nuvo.set_mute(zone_id, mute)
        else:
            await self._nuvo.set_mute(self._zone_id, mute)

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level, range 0..1.

        This has to accept HA 0..1 levels of volume
        and do the conversion to Nuvo volume format.
        """

        nuvo_volume = self._hass_to_nuvo_vol(volume)
        if self.zone_is_group_controller:
            for zone_id in self._nuvo_group_members_zone_ids:
                await self._nuvo.set_volume(zone_id, nuvo_volume)
        else:
            await self._nuvo.set_volume(self._zone_id, nuvo_volume)

    async def async_volume_up(self) -> None:
        """Volume up the media player."""
        if self.zone_is_group_controller:
            for zone_id in self._nuvo_group_members_zone_ids:
                await self._nuvo.volume_up(zone_id)
        else:
            await self._nuvo.volume_up(self._zone_id)

    async def async_volume_down(self) -> None:
        """Volume down media player."""
        if self.zone_is_group_controller:
            for zone_id in self._nuvo_group_members_zone_ids:
                await self._nuvo.volume_down(zone_id)
        else:
            await self._nuvo.volume_down(self._zone_id)

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

        controller_changed = False

        """ Join this zone to the group and make it the Controller."""
        if self._nuvo_group_controller != self.entity_id:
            if self.group_id:
                group = self.group_id
            else:
                group = GROUP_MEMBER
            _LOGGER.debug(
                "GROUPING:JOIN:MAKE_CONTROLLER Controller:zone %d %s/group:%d",
                self._zone_id,
                self.entity_id,
                group,
            )

            # State change
            # Emit event to other members
            controller_changed = True
            self._nuvo_group_controller = self.entity_id

        if not self.group_id:
            _LOGGER.debug(
                "GROUPING:JOIN:CONTROLLER_JOIN_GROUP Controller:zone %d %s/group:%d",
                self._zone_id,
                self.entity_id,
                GROUP_MEMBER,
            )
            await self._process_zone_configuration(
                await self._nuvo.zone_join_group(self._zone_id, GROUP_MEMBER)
            )

        # Switch on the group master if necessary
        if self.state == STATE_OFF:
            """If the zone is off process the ZoneStatus message now as the source and
            volume_level are required for sending in the join_group event."""
            self._process_zone_status(await self._nuvo.set_power(self._zone_id, True))

        if controller_changed:
            """Include this zone in the list of notifyees so HA in turn is notified of
            group_controller state change."""
            members_to_notify = self.group_members
            if members_to_notify:
                _LOGGER.debug(
                    "GROUPING:JOIN:FIRE_GROUP_CONTROLLER_CHANGED Controller:zone %d %s/group:%d/members: %s",
                    self._zone_id,
                    self.entity_id,
                    self.group_id,
                    members_to_notify,
                )
            for entity_id in members_to_notify:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_group_controller_changed",
                    {
                        "target_entity": entity_id,
                        "group": self.group_id,
                        "group_controller": self.group_controller,
                    },
                )

        """Fire a join_group event for each group_member entity so each zone can handle
        joining the group and updating its group state via a ZoneConfiguration
        message."""
        zones_to_group = set(group_members).difference({self.entity_id})
        if zones_to_group:
            _LOGGER.debug(
                "GROUPING:JOIN:FIRE_JOIN_GROUP Controller:zone %d %s/group:%d/joiners: %s",
                self._zone_id,
                self.entity_id,
                self.group_id,
                zones_to_group,
            )
            for entity_id in zones_to_group:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_join_group",
                    {
                        "target_entity": entity_id,
                        "group": self.group_id,
                        "group_controller": self.group_controller,
                        "source": self.source,
                        "volume": self.volume_level,
                    },
                )

    async def async_unjoin_player(self) -> None:
        """Remove this player from any group."""

        if self.zone_is_group_controller:
            """If the group_controller is removed from the group, disband the entire
            group.
            To remove a group_controller zone without disbanding the group, first make
            a new group_controller by sending
            async_join_player message to the the zone to be the new controller, with
            its own entity_id in the group_members, then async_unjoin_player to the
            previous group_controller zone.
            """
            await self._disband_group()
        else:
            _LOGGER.debug(
                "GROUPING:UNJOIN Controller:zone %s/group:%d/removing zone:%d %s",
                self.group_controller,
                self.group_id,
                self._zone_id,
                self.entity_id,
            )
            await self._nuvo.zone_join_group(self._zone_id, GROUP_NON_MEMBER)

    async def _disband_group(self):
        """Remove all zones from this zone's group."""

        _LOGGER.debug(
            "GROUPING:UNJOIN_DISBAND_GROUP Due to Controller leaving group: Controller:zone %s/group:%d/members %s",
            self.group_controller,
            self.group_id,
            self.group_members,
        )
        for zone_id in self._nuvo_group_members_zone_ids:
            await self._nuvo.zone_join_group(zone_id, GROUP_NON_MEMBER)

    async def _group_membership_changed_cb(self, event) -> None:
        """Event callback for a zone to retrieve its list of group members."""

        if event.data["group"] == self._nuvo_group_id:
            _LOGGER.debug(
                "GROUPING:EVENT:GROUP_MEMBER_CHANGE Controller:zone %s/group:%d/member zone:%d %s",
                self.group_controller,
                event.data["group"],
                self._zone_id,
                self.entity_id,
            )
            await self._get_group_members()
            self.async_schedule_update_ha_state()

    async def _group_controller_changed_cb(self, event) -> None:
        """Event callback for a zone to update its group controller."""

        if event.data["target_entity"] != self.entity_id:
            return

        self._nuvo_group_controller = event.data["group_controller"]

        _LOGGER.debug(
            "GROUPING:EVENT:GROUP_CONTROLLER_CHANGE New Controller:zone %s/group:%d/this member zone:%d %s",
            self.group_controller,
            event.data["group"],
            self._zone_id,
            self.entity_id,
        )
        self.async_schedule_update_ha_state()

    async def _nuvo_join_group_event_cb(self, event) -> None:
        """Event callback to join this zone to a group.

        Event emitted by a grouping service call handler.
        """

        if event.data["target_entity"] != self.entity_id:
            return

        _LOGGER.debug(
            "zone %d notified to join group %s",
            self._zone_id,
            event.data,
        )

        self._nuvo_group_controller = event.data["group_controller"]
        await self._process_zone_configuration(
            await self._nuvo.zone_join_group(self._zone_id, event.data["group"])
        )

        if self.state == STATE_OFF:
            # Need to get the source and volume now rather than wait for the state
            # update event callbacks to run so the info can be included in the event
            # sent to the other zones to be grouped.
            self._process_zone_status(await self._nuvo.set_power(self._zone_id, True))

        sync_source = event.data["source"]
        if sync_source in self.source_list and self.source != sync_source:
            await self.async_select_source(sync_source)

        if event.data["volume"]:
            if self.is_volume_muted:
                await self.async_mute_volume(False)
            await self.async_set_volume_level(event.data["volume"])
        else:
            if not self.is_volume_muted:
                await self.async_mute_volume(True)

    async def _get_group_members(self) -> None:
        """Retrieve this zone's group member zone_ids from Nuvo and convert to entity_ids."""

        member_entity_ids = []
        group_member_zone_ids = await self._nuvo.zone_group_members(self._zone_id)
        for z_id in group_member_zone_ids:
            entity_id = await self._nuvo_zone_id_to_hass_entity_id(z_id)
            if entity_id:
                member_entity_ids.append(entity_id)

        self._nuvo_group_members = member_entity_ids
        self._nuvo_group_members_zone_ids = group_member_zone_ids
        _LOGGER.debug(
            "GROUPING:GET_GROUP_MEMBERS for zone %d %s/found:%s",
            self._zone_id,
            self.entity_id,
            self.group_members,
        )

    async def _nuvo_zone_id_to_hass_entity_id(self, zone_id) -> str | None:
        """Get the hass entity_id from the nuvo zone_id."""
        mp_states = self.hass.states.async_all(MP_DOMAIN)
        entity_id = None
        for ent_state in mp_states:
            z_id = ent_state.attributes.get("zone_id")
            if z_id and z_id == zone_id:
                entity_id = ent_state.entity_id
                break

        return entity_id

    async def async_media_play_pause(self):
        """Play or pause the media player."""
        await self._nuvo.zone_button_play_pause(self._zone_id)

    async def async_media_next_track(self):
        """Send next track command."""
        await self._nuvo.zone_button_next(self._zone_id)

    async def async_media_previous_track(self):
        """Send previous track command."""
        await self._nuvo.zone_button_prev(self._zone_id)

    async def snapshot(self) -> None:
        """Service handler to save zone's current state."""
        self._snapshot = await self._nuvo.zone_status(self._zone_id)

    async def restore(self) -> None:
        """Service handler to restore zone's saved state."""
        if self._snapshot:
            await self._nuvo.restore_zone(self._snapshot)

    async def party_on(self) -> None:
        """Service call to make this zone the party host."""
        await self._nuvo.set_party_host(self._zone_id, True)

    async def party_off(self) -> None:
        """Service call to release this zone from being the party host."""
        await self._nuvo.set_party_host(self._zone_id, False)
