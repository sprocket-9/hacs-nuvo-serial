"""Support for interfacing with Nuvo multi-zone amplifier."""
from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_HALF_EVEN, Decimal
import logging
from typing import Any
from uuid import uuid4

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
    ZONE,
)
from .helpers import get_sources, get_zones

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


def group_membership_check(func):
    """Decorate a service call func with the nuvo connection associated with a service call device_id.

    If there are multiple nuvo_serial integrations installed this ensures the
    service call is made to the correct system.
    """

    async def membership_check(self, event):
        if event.data["target_entity"] != self.entity_id:
            return

        if (
            not self.group_controller
            or self.group_controller != event.data["group_controller"]
        ):
            return

        if not self.group_id or self.group_id != event.data["group"]:
            return

        await func(self, event)

    return membership_check


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
        self._nuvo = nuvo
        self._port = port
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
        self._volume_step = volume_step
        self._max_volume = max_volume
        self._min_volume = min_volume

        self._snapshot = None
        self._state: str | None = None
        self._volume: float | None = None
        self._source: str | None = None
        self._mute: bool | None = None
        self._nuvo_group_members: list[str] = []
        # self._nuvo_group_members_zone_ids: set[int] = set()
        # self._nuvo_group_id: int | None = None
        self._nuvo_group_id: str = ""
        self._nuvo_group_controller: str = ""
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
        return f"{self._namespace}_zone_{self._zone_id}_zone"

    @property
    def name(self) -> str | None:
        """Return the name of the zone."""
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the name of the control."""
        return {
            "zone_id": self._zone_id,
            "nuvo_group_id": self._nuvo_group_id,
            "nuvo_group_controller": self._nuvo_group_controller,
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
        return self._nuvo_group_members

    @property
    def group_controller(self) -> str:
        """Return entity_id of the zone that is the group controller.

        This zone will control volume sync for the group.
        """
        return self._nuvo_group_controller

    @property
    def group_id(self) -> str:
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

        self._events_removers.append(
            self.hass.bus.async_listen(
                f"{DOMAIN}_group_controller_changed", self._group_controller_changed_cb
            )
        )

        self._events_removers.append(
            self.hass.bus.async_listen(
                f"{DOMAIN}_group_update_member_list", self._group_update_member_list_cb
            )
        )

        self._events_removers.append(
            self.hass.bus.async_listen(
                f"{DOMAIN}_join_group", self._nuvo_join_group_event_cb
            )
        )

        self._events_removers.append(
            self.hass.bus.async_listen(
                f"{DOMAIN}_unjoin_group", self._nuvo_unjoin_group_event_cb
            )
        )

        self._events_removers.append(
            self.hass.bus.async_listen(
                f"{DOMAIN}_group_controller_volume_changed",
                self._group_controller_volume_changed_cb,
            )
        )

        self._events_removers.append(
            self.hass.bus.async_listen(
                f"{DOMAIN}_group_controller_mute_changed",
                self._group_controller_mute_changed_cb,
            )
        )

        self._events_removers.append(
            self.hass.bus.async_listen(
                f"{DOMAIN}_group_disband",
                self._nuvo_disband_group_event_cb,
            )
        )

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

        for remove_event_listener in self._events_removers:
            remove_event_listener()

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
            self.async_schedule_update_ha_state()
        elif event_name == ZONE_STATUS:
            state_changes = self._process_zone_status(d_class)
            self.async_schedule_update_ha_state()
            # Allow this task to finish by continuing group state processing in a new
            # task
            self.hass.async_create_task(self._process_state_changes(state_changes))

    async def _process_state_changes(self, state_changes: dict[str, bool]) -> None:
        """Task to handle state changes for group members."""
        if state_changes["power"]:
            if self.zone_is_group_controller and self._state == STATE_OFF:
                await self._group_controller_power_change()
            elif self.entity_id in self.group_members and self._state == STATE_OFF:
                await self._group_member_power_change()

        else:
            if self.zone_is_group_controller:
                if state_changes["mute"]:
                    self._notify_group_of_mute_change()
                if state_changes["volume"]:
                    self._notify_group_of_volume_change()

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

    def _process_zone_status(self, z_status: ZoneStatus) -> dict[str, bool]:
        """Update zone's power, volume and source state.

        A permitted source may not appear in the list of system-wide enabled sources.
        """

        state_changes = {"power": False, "mute": False, "volume": False}

        state_changes["power"] = self._process_power(z_status.power)

        if self._state == STATE_OFF:
            self._mute = None
            self._volume = None
            self._source = None
            return state_changes

        self._source = self._source_id_name.get(z_status.source, None)

        state_changes["mute"] = self._process_mute(z_status.mute)

        if self._mute:
            return state_changes

        state_changes["volume"] = self._process_volume(z_status.volume)
        return state_changes

    def _process_power(self, received_power_state: bool) -> bool:
        _power_changed = False
        if self._state is None:
            self._state = self.power_as_state[received_power_state]
        else:
            if (
                _power_changed := self.state_as_power[self._state]
                != received_power_state
            ):
                self._state = self.power_as_state[received_power_state]

        return _power_changed

    def _process_mute(self, received_mute_state: bool) -> bool:
        """Process zone's mute status."""

        # Zone is ON here so received_mute_state will be a bool
        _mute_changed = False
        if self._mute is None:
            self._mute = received_mute_state
        else:
            if _mute_changed := self._mute != received_mute_state:
                self._mute = received_mute_state

        if self._mute:
            self._volume = None

        return _mute_changed

    def _process_volume(self, received_nuvo_volume: bool) -> bool:
        """Process zone's volume status."""
        received_volume = self._nuvo_to_hass_vol(received_nuvo_volume)

        # Zone is ON here so received_volume will be a bool
        _volume_changed = False
        if self._volume is None:
            self._volume = received_volume
        else:
            if _volume_changed := self._volume != received_volume:
                self._volume = received_volume

        return _volume_changed

    def _notify_group_of_mute_change(self):
        """Notify group members the group_controller has changed mute state."""

        if members_to_notify := set(self.group_members).difference({self.entity_id}):
            _LOGGER.debug(
                "GROUPING:EVENT:CONTROLLER:FIRE_GROUP_CONTROLLER_MUTE_CHANGED From Controller:zone %d %s/group:%s/members: %s/mute: %s",
                self._zone_id,
                self.entity_id,
                self.group_id,
                members_to_notify,
                str(self._mute),
            )
            for entity_id in members_to_notify:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_group_controller_mute_changed",
                    {
                        "target_entity": entity_id,
                        "group": self.group_id,
                        "group_controller": self.group_controller,
                        "mute": self._mute,
                    },
                )

    def _notify_group_of_volume_change(self):
        """Notify group members the group_controller has changed volume."""

        if members_to_notify := set(self.group_members).difference({self.entity_id}):
            _LOGGER.debug(
                "GROUPING:EVENT:CONTROLLER:FIRE_GROUP_CONTROLLER_VOLUME_CHANGED From Controller:zone %d %s/group:%s/members: %s/volume: %f",
                self._zone_id,
                self.entity_id,
                self.group_id,
                members_to_notify,
                self._volume,
            )
            for entity_id in members_to_notify:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_group_controller_volume_changed",
                    {
                        "target_entity": entity_id,
                        "group": self.group_id,
                        "group_controller": self.group_controller,
                        "volume": self._volume,
                    },
                )

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

    async def _group_controller_power_change(self):
        """Notify group members the group_controller has switched off."""
        zones_to_notify = set(self.group_members).difference({self.entity_id})
        if zones_to_notify:
            _LOGGER.debug(
                "GROUPING:UNJOIN:CONTROLLER:FIRE_DISBAND_GROUP_FROM_CONTROLLER_POWER_OFF This Controller:zone %d %s/group:%s/members: %s",
                self._zone_id,
                self.entity_id,
                self.group_id,
                zones_to_notify,
            )
            for entity_id in zones_to_notify:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_group_disband",
                    {
                        "target_entity": entity_id,
                        "group": self._nuvo_group_id,
                        "group_controller": self.group_controller,
                    },
                )
            self._clear_nuvo_group_info()
            self.async_schedule_update_ha_state()

    async def _group_member_power_change(self):
        """Handle a power off event for a group member."""
        _LOGGER.debug(
            "GROUPING:UNJOIN:POWER_OFF:REMOVE_MEMBER Removing this zone due to it powering off:%d %s/Controller:zone %s/group:%s/",
            self._zone_id,
            self.entity_id,
            self.group_controller,
            self.group_id,
        )
        self._unjoin_member_from_group()
        self.async_schedule_update_ha_state()

    def _unjoin_member_from_group(self):
        """Remove this zone from its speaker group."""

        zones_to_notify = self.group_members.copy()
        self._remove_member_from_group_members(self.entity_id, zones_to_notify)
        group = self.group_id

        self._clear_nuvo_group_info()

        if len(zones_to_notify) == 1:
            # disband group by notifying group_controller to leave
            self._fire_unjoin_group(zones_to_notify, group)
        else:
            self._fire_member_list_change(zones_to_notify, zones_to_notify, group)

    def _clear_nuvo_group_info(self):
        self._nuvo_group_id = ""
        self._nuvo_group_controller = ""
        self._nuvo_group_members = []
        # self._nuvo_group_members_zone_ids = set()

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
        await self._nuvo.volume_up(self._zone_id)

    async def async_volume_down(self) -> None:
        """Volume down media player."""
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
        zones_to_add: Iterable[str] | None = None
        zones_to_remove = None
        group = str(uuid4())
        # MMP Group All button also includes the card's entity_id in group members,
        # remove this
        self._remove_member_from_group_members(self.entity_id, group_members)
        sorted_group_members = group_members.copy()
        sorted_group_members.insert(0, self.entity_id)

        # Switch on the zone if necessary
        if self.state == STATE_OFF:
            # If the zone is off process the ZoneStatus message now as the source and
            # volume_level are required for sending in the join_group event.

            self._process_zone_status(await self._nuvo.set_power(self._zone_id, True))

        if self._nuvo_group_controller == self.entity_id:
            # This zone is already a group controller but group_members may be different
            # than the existing members.
            group = self.group_id
            zones_to_add = set(group_members).difference(self.group_members)
            # zones_to_remove = set(self.group_members).difference({self.entity_id}).difference(group_members)
            # sorted_group_members.extend(zones_to_add)
            # sorted_group_members = self.group_members.extend(zones_to_add)
            sorted_group_members = list(
                set(self.group_members).difference({self.entity_id}).union(zones_to_add)
            )
            sorted_group_members.insert(0, self.entity_id)

        elif (
            self._nuvo_group_controller
            and self._nuvo_group_controller != self.entity_id
        ):
            # This zone is already in a group with another zone being the controller.

            # The existing controller may be in group_members.

            # If not, the other controller can continue being a controller if there's
            # at least one other member remaining in the group
            zones_to_add = set(group_members).difference({self.entity_id})
            if self._nuvo_group_controller in zones_to_add:
                # This zone is taking over the group from the previous controller.
                # The previous controller will take care of removing its members, some
                # of which may be in group_members.  No problem here though, if the
                # group controller notifies a member to leave and that member has already
                # changed group then it's a noop.
                pass
            else:
                # This zone's existing controller is not being added to the new group
                # zones_remaining_in_previous_group = set(self.group_members).difference({self.entity_id}).difference(group_members).difference({self._nuvo_group_controller})
                zones_remaining_in_previous_group = (
                    set(self.group_members)
                    .difference({self.entity_id})
                    .difference(group_members)
                )
                if not zones_remaining_in_previous_group.difference(
                    {self._nuvo_group_controller}
                ):
                    # Disband group by telling the group_controller to leave its group
                    zones_to_remove = set({self._nuvo_group_controller})
                else:
                    # Update members list for remaining group members
                    _LOGGER.debug(
                        "GROUPING:JOIN:CONTROLLER:NOTIFY_PREVIOUS_GROUP_OF_NEW_MEMBER_LIST This new controller: zone %d %s/group:%s/notifyees: %s",
                        self._zone_id,
                        self.entity_id,
                        group,
                        zones_remaining_in_previous_group,
                    )
                    self._fire_member_list_change(
                        zones_remaining_in_previous_group,
                        zones_remaining_in_previous_group,
                        group,
                    )

        else:
            # Zone is not in an existing group
            # Make this zone the group controller.
            zones_to_add = group_members

            _LOGGER.debug(
                "GROUPING:JOIN:CONTROLLER:MAKE_NEW_CONTROLLER this zone is now a new controller: zone %d %s/group:%s",
                self._zone_id,
                self.entity_id,
                group,
            )
            self._nuvo_group_controller = self.entity_id

        if zones_to_remove:
            _LOGGER.debug(
                "GROUPING:UNJOIN:CONTROLLER:FIRE_UNJOIN_GROUP This Controller:zone %d %s/group:%s/leavers: %s",
                self._zone_id,
                self.entity_id,
                self.group_id,
                zones_to_remove,
            )
            self._fire_unjoin_group(zones_to_remove, self.group_id)

        # Fire a join_group event for each group_member entity so each zone can handle
        # joining the group
        if zones_to_add:
            _LOGGER.debug(
                "GROUPING:JOIN:CONTROLLER:FIRE_JOIN_GROUP This Controller:zone %d %s/group:%s/joiners: %s",
                self._zone_id,
                self.entity_id,
                group,
                zones_to_add,
            )
            for entity_id in zones_to_add:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_join_group",
                    {
                        "target_entity": entity_id,
                        "group": group,
                        "group_members": sorted_group_members,
                        "group_controller": self.group_controller,
                        "source": self.source,
                        "volume": self.volume_level,
                    },
                )

        self._nuvo_group_id = group
        self._nuvo_group_controller = self.entity_id
        self._nuvo_group_members = sorted_group_members
        self.async_schedule_update_ha_state()

    async def async_unjoin_player(self) -> None:
        """Remove this player from any group."""

        if self.zone_is_group_controller:
            # If the group_controller is removed from the group, disband the entire
            # group.
            # To remove a group_controller zone without disbanding the group, first make
            # a new group_controller by sending
            # async_join_player message to the the zone to be the new controller, with
            # its own entity_id in the group_members, then async_unjoin_player to the
            # previous group_controller zone.
            # self._disband_group()
            # self._clear_nuvo_group_info()
            pass
        else:
            _LOGGER.debug(
                "GROUPING:UNJOIN_PLAYER_SERVICE_CALL:REMOVE_MEMBER Removing this member zone:%d %s/Controller:zone %s/group:%s/",
                self._zone_id,
                self.entity_id,
                self.group_controller,
                self.group_id,
            )
            # ZoneStatus processing will take care of removing from group and notifying
            # group members of change.
            self._unjoin_member_from_group()

        self.async_schedule_update_ha_state()
        await self.async_turn_off()

    def _fire_member_list_change(
        self, zones: Iterable[str], group_members: Iterable[str], group: str
    ) -> None:
        """Notify zones that group_members is their group list."""
        for entity_id in zones:
            self.hass.bus.async_fire(
                f"{DOMAIN}_group_update_member_list",
                {
                    "target_entity": entity_id,
                    "group": group,
                    "group_members": group_members,
                },
            )

    def _disband_group(self):
        """Remove all zones from this zone's group."""

        _LOGGER.debug(
            "GROUPING:UNJOIN:CONTROLLER:DISBAND_GROUP Due to Controller leaving group: This Controller:zone %s/group:%s/members %s",
            self.group_controller,
            self.group_id,
            self.group_members,
        )

        zones_to_notify = set(self.group_members).difference(self.entity_id)
        self._fire_unjoin_group(zones_to_notify, self.group_id)

    def _fire_unjoin_group(self, zones: Iterable[str], group_id: str) -> None:
        """Fire unjoin_group event."""

        for entity_id in zones:
            self.hass.bus.async_fire(
                f"{DOMAIN}_unjoin_group",
                {
                    "target_entity": entity_id,
                    "group": group_id,
                },
            )

    async def _group_update_member_list_cb(self, event) -> None:
        """Event callback for a zone to update its member list."""

        if event.data["target_entity"] != self.entity_id:
            return
        if event.data["group"] != self.group_id:
            return

        self._nuvo_group_members = event.data["group_members"]

        _LOGGER.debug(
            "GROUPING:EVENT:NEW_MEMBER_LIST This member zone:%d %s/Controller:zone %s/group:%s/members:%s",
            self._zone_id,
            self.entity_id,
            self.group_controller,
            event.data["group"],
            event.data["group_members"],
        )
        self.async_schedule_update_ha_state()

    async def _group_controller_changed_cb(self, event) -> None:
        """Event callback for a zone to update its group controller."""

        if event.data["target_entity"] != self.entity_id:
            return

        self._nuvo_group_controller = event.data["group_controller"]

        _LOGGER.debug(
            "GROUPING:EVENT:MEMBER:GROUP_CONTROLLER_CHANGE This member zone:%d %s/New Controller:zone %s/group:%s/",
            self._zone_id,
            self.entity_id,
            self.group_controller,
            event.data["group"],
        )
        self.async_schedule_update_ha_state()

    async def _nuvo_unjoin_group_event_cb(self, event) -> None:
        """Event callback to unjoin this zone from its group."""

        if event.data["target_entity"] != self.entity_id:
            return
        if event.data["group"] != self.group_id:
            return

        if self.zone_is_group_controller:
            zones_to_remove = set(self.group_members).difference({self.entity_id})
            _LOGGER.debug(
                "GROUPING:EVENT:CONTROLLER_UNJOIN_GROUP: This controller has been notified to leave and disband its group: zone %d %s/group:%s/leavers: %s",
                self._zone_id,
                self.entity_id,
                self.group_id,
                zones_to_remove,
            )
            self._fire_unjoin_group(zones_to_remove, self.group_id)
        else:
            _LOGGER.debug(
                "GROUPING:EVENT:MEMBER_UNJOIN_GROUP This zone: %d %s/controller: %s/group:%s",
                self._zone_id,
                self.entity_id,
                self.group_controller,
                self.group_id,
            )

        self._clear_nuvo_group_info()
        self.async_schedule_update_ha_state()

    async def _nuvo_join_group_event_cb(self, event) -> None:
        """Event callback to join this zone to a group.

        Event emitted by a grouping service call handler.
        """

        if event.data["target_entity"] != self.entity_id:
            return

        zones_to_notify = set()

        if self.group_members:
            # Zone is in an existing group

            # new_member_list = set(self.group_members).difference({self.entity_id})
            new_member_list = self.group_members.copy()

            self._remove_member_from_group_members(self.entity_id, new_member_list)

            if self.zone_is_group_controller:
                _LOGGER.debug(
                    "GROUPING:EVENT:CONTROLLER_CHANGE_GROUP This (previous controller) zone: %d %s/old group:%s/new controller: %s/new group:%s",
                    self._zone_id,
                    self.entity_id,
                    self.group_id,
                    event.data["group_controller"],
                    event.data["group"],
                )
                # Disband the existing group
                _LOGGER.debug(
                    "GROUPING:DISBAND:CONTROLLER:FIRE_DISBAND_GROUP_FROM_CONTROLLER_JOINING_ANOTHER_GROUP This Controller:zone %d %s/group:%s/notifyees: %s",
                    self._zone_id,
                    self.entity_id,
                    self.group_id,
                    new_member_list,
                )
                self._fire_unjoin_group(new_member_list, self.group_id)

                zones_to_notify = set(event.data["group_members"]).difference(
                    {self.entity_id, event.data["group_controller"]}
                )

                if zones_to_notify:
                    _LOGGER.debug(
                        "GROUPING:EVENT:MEMBER_JOINED_NEW_GROUP:NOTIFY_GROUP_MEMBERS_OF_NEW_MEMBER_LIST This new member zone: zone %d %s/group:%s/notifyees: %s",
                        self._zone_id,
                        self.entity_id,
                        self.group_id,
                        zones_to_notify,
                    )
                    # self._fire_member_list_change(zones_to_notify, event.data["group_members"], event.data["group"])

            else:
                # Zone is in an existing group and joining a different group
                _LOGGER.debug(
                    "GROUPING:EVENT:MEMBER_CHANGE_GROUP This zone: %d %s/controller: %s/group:%s",
                    self._zone_id,
                    self.entity_id,
                    event.data["group_controller"],
                    event.data["group"],
                )
                # Notify previous group members this zone has left
                _LOGGER.debug(
                    "GROUPING:CHANGE_GROUP:MEMBER:NOTIFY_PREVIOUS_GROUP_OF_NEW_MEMBER_LIST This previous member zone: zone %d %s/group:%s/notifyees: %s",
                    self._zone_id,
                    self.entity_id,
                    self.group_id,
                    new_member_list,
                )
                if len(new_member_list) == 1:
                    # If there's only one member remaining in previous group, disband group
                    # by notifying remaining member to leave its group
                    self._fire_unjoin_group(new_member_list, self.group_id)
                else:
                    self._fire_member_list_change(
                        new_member_list, new_member_list, self.group_id
                    )

                # Notify the new group members this zone has joined
                zones_to_notify = set(event.data["group_members"]).difference(
                    {self.entity_id, event.data["group_controller"]}
                )
                if zones_to_notify:
                    _LOGGER.debug(
                        "GROUPING:EVENT:MEMBER_JOINED_NEW_GROUP:NOTIFY_GROUP_MEMBERS_OF_NEW_MEMBER_LIST This new member zone: zone %d %s/group:%s/notifyees: %s",
                        self._zone_id,
                        self.entity_id,
                        self.group_id,
                        zones_to_notify,
                    )
                    # self._fire_member_list_change(zones_to_notify, event.data["group_members"], event.data["group"])
        else:
            # Zone is not in an existing group
            _LOGGER.debug(
                "GROUPING:EVENT:MEMBER_JOINED_NEW_GROUP This new member zone: %d %s/controller: %s/group:%s",
                self._zone_id,
                self.entity_id,
                event.data["group_controller"],
                event.data["group"],
            )
            zones_to_notify = set(event.data["group_members"]).difference(
                {self.entity_id, event.data["group_controller"]}
            )

            if zones_to_notify:
                _LOGGER.debug(
                    "GROUPING:EVENT:MEMBER_JOINED_NEW_GROUP:NOTIFY_GROUP_MEMBERS_OF_NEW_MEMBER_LIST This new member zone: zone %d %s/group:%s/notifyees: %s",
                    self._zone_id,
                    self.entity_id,
                    self.group_id,
                    zones_to_notify,
                )
                # self._fire_member_list_change(zones_to_notify, event.data["group_members"], event.data["group"])

        if zones_to_notify:
            # Now this zone is part of the group, update the other group members of the
            # new member list
            self._fire_member_list_change(
                zones_to_notify, event.data["group_members"], event.data["group"]
            )

        self._nuvo_group_id = event.data["group"]
        self._nuvo_group_controller = event.data["group_controller"]
        self._nuvo_group_members = event.data["group_members"]
        self.async_schedule_update_ha_state()

        # self._nuvo_group_controller = event.data["group_controller"]
        # await self._process_zone_configuration(
        #     await self._nuvo.zone_join_group(self._zone_id, event.data["group"])
        # )

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

    @group_membership_check
    async def _group_controller_volume_changed_cb(self, event) -> None:
        """Event callback for a zone to sync its volume with the group controller."""

        if self.volume_level != event.data["volume"]:
            _LOGGER.debug(
                "GROUPING:EVENT:MEMBER:VOLUME_SYNC_WITH_CONTROLLER: This member zone:%d %s/controller %s/group:%s/volume:%f",
                self._zone_id,
                self.entity_id,
                self.group_controller,
                event.data["group"],
                event.data["volume"],
            )
            if self.is_volume_muted:
                await self.async_mute_volume(False)
            await self.async_set_volume_level(event.data["volume"])

            self.async_schedule_update_ha_state()

    @group_membership_check
    async def _group_controller_mute_changed_cb(self, event) -> None:
        """Event callback for zone to sync mute status with the group controller."""

        if self._mute != event.data["mute"]:
            _LOGGER.debug(
                "GROUPING:EVENT:MEMBER:MUTE_SYNC_WITH_CONTROLLER: This member zone:%d %s/controller %s/group:%s/mute:%s",
                self._zone_id,
                self.entity_id,
                self.group_controller,
                event.data["group"],
                str(event.data["mute"]),
            )
            await self.async_mute_volume(event.data["mute"])
            self.async_schedule_update_ha_state()

    async def _nuvo_disband_group_event_cb(self, event) -> None:
        """Event callback to leave the group and switch off."""

        if event.data["target_entity"] != self.entity_id:
            return

        _LOGGER.debug(
            "GROUPING:EVENT:MEMBER_DISBAND_GROUP This zone: %d %s/controller: %s/group:%s",
            self._zone_id,
            self.entity_id,
            event.data["group_controller"],
            self.group_id,
        )
        await self.async_unjoin_player()

    # async def _get_group_members(self) -> None:
    #     """Retrieve this zone's group member zone_ids from Nuvo and convert to entity_ids."""
    #
    #     member_entity_ids = []
    #     group_member_zone_ids = await self._nuvo.zone_group_members(self._zone_id)
    #     for z_id in group_member_zone_ids:
    #         entity_id = await self._nuvo_zone_id_to_hass_entity_id(z_id)
    #         if entity_id:
    #             member_entity_ids.append(entity_id)
    #
    #     self._nuvo_group_members = member_entity_ids
    #     self._order_group_members()
    #     # self._nuvo_group_members_zone_ids = group_member_zone_ids
    #     _LOGGER.debug(
    #         "GROUPING:GET_GROUP_MEMBERS for this zone %d %s/found:%s",
    #         self._zone_id,
    #         self.entity_id,
    #         self.group_members,
    #     )

    def _remove_member_from_group_members(
        self, entity_id: str, group_members: list[str]
    ) -> None:
        """Remove entity_id from group_members."""
        try:
            group_members.remove(entity_id)
        except ValueError:
            pass

    def _order_group_members(self):
        """Make the group_controller the first element in the list of group_members."""

        if self.group_controller and self.group_controller in self._nuvo_group_members:
            self._nuvo_group_members.remove(self.group_controller)
            self._nuvo_group_members.insert(0, self.group_controller)

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

    async def simulate_play_pause_button(self) -> None:
        """Service call to simulate pressing keypad play/pause button."""
        await self._nuvo.zone_button_play_pause(self._zone_id)

    async def simulate_prev_button(self) -> None:
        """Service call to simulate pressing keypad prev button."""
        await self._nuvo.zone_button_prev(self._zone_id)

    async def simulate_next_button(self) -> None:
        """Service call to simulate pressing keypad next button."""
        await self._nuvo.zone_button_next(self._zone_id)
