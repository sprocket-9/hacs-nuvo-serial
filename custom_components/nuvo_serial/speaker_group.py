"""Speaker group implementation for Nuvo zones."""

from collections.abc import Callable, Iterable
from enum import Enum, auto
import logging
from uuid import uuid4

from homeassistant.const import STATE_OFF
from homeassistant.core import Event

from .const import (
    SPEAKER_GROUP_CONTROLLER_MUTE_CHANGED,
    SPEAKER_GROUP_CONTROLLER_SOURCE_CHANGED,
    SPEAKER_GROUP_CONTROLLER_VOLUME_CHANGED,
    SPEAKER_GROUP_JOIN,
    SPEAKER_GROUP_MEMBER_LIST_JOINED,
    SPEAKER_GROUP_MEMBER_LIST_LEFT,
)

_LOGGER = logging.getLogger(__name__)


def group_member_check(func: Callable):
    """Filter to ensure only valid zones process a speaker group event.

    Zone:
        Is NOT the controller of the group in the event.
        Is a member of the group in the event.
        Is NOT the zone which emitted the event.
    """

    async def membership_check(self, event):
        if not self.zone_is_group_member:
            return

        if not self.group_id or self.group_id != event.data["group"]:
            return

        if event.data["sender"] == self.zone.entity_id:
            return

        await func(self, event)

    return membership_check


def group_member_or_controller_check(func: Callable):
    """Filter to ensure only valid zones process a speaker group event.

    Zone:
        Is the controller OR a member of the group in the event.
        Is NOT the zone which emitted the event.
    """

    async def membership_check(self, event):
        if self.zone_is_group_non_member:
            return

        if not self.group_id or self.group_id != event.data["group"]:
            return

        if event.data["sender"] == self.zone.entity_id:
            return

        await func(self, event)

    return membership_check


class GroupStatus(Enum):
    """Representation of a zone's speaker group membership status."""

    NONMEMBER = auto()
    MEMBER = auto()
    CONTROLLER = auto()


class SpeakerGroup:
    """Representation of a group of zones acting as one synchronised speaker group.

    One zone acts as the speaker group controller - changes in this zone's power,
    volume, mute and source selection state are mirrored by the other group members.

    Groups are ephemeral and do not persist between HA restarts.

    Speaker groups are implemented solely within HA and do not use the Nuvo system
    group (source selection synchronisation) feature.
    """

    def __init__(self, zone):
        """Initialize zone's speaker group status."""
        self.zone = zone
        self.group_id: str = ""
        self.group_controller: str = ""
        self.group_members: list[str] = []
        self._group_status = GroupStatus.NONMEMBER

    @property
    def zone_is_group_controller(self) -> bool:
        """Return the zone's group controller status."""
        return self._group_status is GroupStatus.CONTROLLER

    @property
    def zone_is_group_member(self) -> bool:
        """Return the zone's group membership status."""
        return self._group_status is GroupStatus.MEMBER

    @property
    def zone_is_group_non_member(self) -> bool:
        """Return the zone's group non-membership status."""
        return self._group_status is GroupStatus.NONMEMBER

    async def async_join_players(self, group_members: list[str]):
        """Join `group_members` as a player group with the current player."""

        zones_to_add: Iterable[str] = []
        group = str(uuid4())

        # Mini media player's Group All button also includes the card's entity_id in
        # group members, remove this as it's superfluous.
        if self.zone.entity_id in group_members:
            self._remove_member_from_group_members(self.zone.entity_id, group_members)

        # This zone is becoming a group controller.  Make sure it's always the first
        # element in group_members list as MMP uses this to determine which player
        # it considers the Master in a speaker group.
        sorted_group_members = group_members.copy()
        sorted_group_members.insert(0, self.zone.entity_id)

        # Switch on the zone if necessary
        if self.zone.state == STATE_OFF:
            # If the zone is off process the ZoneStatus message now as the source and
            # volume_level are required for sending in the join_group event.
            self.zone.process_zone_status(
                await self.zone.nuvo.set_power(self.zone.zone_id, True)
            )
            # self.zone.process_zone_status(await self.zone.async_turn_on())

        if self.zone_is_group_controller:
            # This zone is already a group controller.
            # Add group_members into the existing group.
            group = self.group_id
            zones_to_add = set(group_members).difference(self.group_members)
            sorted_group_members = list(
                set(self.group_members)
                .difference({self.zone.entity_id})
                .union(zones_to_add)
            )
            sorted_group_members.insert(0, self.zone.entity_id)
            _LOGGER.debug(
                "GROUPING:JOIN_PLAYER_SERVICE_CALL:ADD_MEMBERS_TO_EXISTING_GROUP This controller zone is adding new members to its group - this controller zone: zone %d %s/group:%s/existing: %s/additions: %s/merged: %s",
                self.zone.zone_id,
                self.zone.entity_id,
                group,
                self.group_members,
                zones_to_add,
                sorted_group_members,
            )

        elif self.zone_is_group_member:
            # Zone will leave existing group and become the controller of a new group.
            zones_to_add = group_members
            _LOGGER.debug(
                "GROUPING:JOIN_PLAYER_SERVICE_CALL:MAKE_NEW_CONTROLLER_FROM_PREVIOUS_GROUP_MEMBER This zone was a member of a group but is now a new controller of a new group: zone %d %s/old group:%s/new group:%s/members:%s",
                self.zone.zone_id,
                self.zone.entity_id,
                self.group_id,
                group,
                sorted_group_members,
            )
            self._fire_member_list_left_event(self.zone.entity_id, self.group_id)

        else:
            # Zone is not in an existing group
            # Make this zone the group controller.
            zones_to_add = group_members

            _LOGGER.debug(
                "GROUPING:JOIN_PLAYER_SERVICE_CALL:MAKE_NEW_CONTROLLER_FROM_GROUP_NON_MEMBER This zone is now a new controller: zone %d %s/group:%s/members:%s",
                self.zone.zone_id,
                self.zone.entity_id,
                group,
                sorted_group_members,
            )

        self._group_status = GroupStatus.CONTROLLER
        self.group_id = group
        self.group_controller = self.zone.entity_id
        self.group_members = sorted_group_members

        # Fire a join_group event for each group_member entity so each zone can handle
        # joining the group
        if zones_to_add:
            _LOGGER.debug(
                "GROUPING:JOIN_PLAYER_SERVICE_CALL:FIRE_JOIN_GROUP This Controller:zone %d %s/group:%s/joiners: %s",
                self.zone.zone_id,
                self.zone.entity_id,
                group,
                zones_to_add,
            )
            for entity_id in zones_to_add:
                self.zone.hass.bus.async_fire(
                    SPEAKER_GROUP_JOIN,
                    {
                        "target_entity": entity_id,
                        "group": group,
                        "group_members": sorted_group_members.copy(),
                        "group_controller": self.group_controller,
                        "source": self.zone.source,
                        "volume": self.zone.volume_level,
                    },
                )

        self.zone.async_write_ha_state()

    async def async_unjoin_player(self) -> None:
        """Remove this player from any group."""

        if self.zone_is_group_non_member:
            return

        _LOGGER.debug(
            "GROUPING:UNJOIN_PLAYER_SERVICE_CALL: Removing this member zone:%d %s/Controller:zone %s/group:%s/",
            self.zone.zone_id,
            self.zone.entity_id,
            self.group_controller,
            self.group_id,
        )

        self._unjoin_member_from_group()
        self.zone.async_write_ha_state()
        await self.zone.async_turn_off()

    async def _group_member_power_change(self):
        """Handle a power off event for a group member."""

        _LOGGER.debug(
            "GROUPING:UNJOIN:POWER_OFF:REMOVE_MEMBER Removing this zone due to it powering off:%d %s/Controller:zone %s/group:%s/",
            self.zone.zone_id,
            self.zone.entity_id,
            self.group_controller,
            self.group_id,
        )
        self._unjoin_member_from_group()
        self.zone.async_write_ha_state()

    def _unjoin_member_from_group(self):
        """Remove this zone from its group."""
        group = self.group_id
        self._clear_group_info()
        self._fire_member_list_left_event(self.zone.entity_id, group)

    def _clear_group_info(self):
        """Remove all group details from this zone."""
        self._group_status = GroupStatus.NONMEMBER
        self.group_id = ""
        self.group_controller = ""
        self.group_members = []

    def _remove_member_from_group_members(
        self, entity_id: str, group_members: list[str]
    ) -> None:
        """Remove entity_id from group_members."""

        try:
            group_members.remove(entity_id)
        except ValueError:
            _LOGGER.warning(
                "GROUPING:REMOVE_MEMBER_NOT_FOUND This zone: %d %s/attempted to remove member: %s/group_members:%s",
                self.zone.zone_id,
                self.zone.entity_id,
                entity_id,
                group_members,
            )

    async def propagate_group_state_changes(
        self, state_change: dict[str, bool]
    ) -> None:
        """Task to handle state changes for group members."""

        if state_change["power"]:
            if self.zone_is_group_controller and self.zone.state == STATE_OFF:
                self._group_controller_power_change()
            elif self.zone_is_group_member and self.zone.state == STATE_OFF:
                await self._group_member_power_change()

        elif self.zone_is_group_controller:
            if state_change["mute"]:
                self._fire_group_controller_mute_changed_event()
            if state_change["volume"]:
                self._fire_group_controller_volume_changed_event()
            if state_change["source"]:
                self._fire_group_controller_source_changed_event()

    def _group_controller_power_change(self):
        """Notify group members the group_controller has switched off."""

        _LOGGER.debug(
            "GROUPING:UNJOIN:CONTROLLER:FIRE_DISBAND_GROUP_FROM_CONTROLLER_POWER_OFF This Controller:zone %d %s/group:%s/members: %s",
            self.zone.zone_id,
            self.zone.entity_id,
            self.group_id,
            self.group_members,
        )

        group = self.group_id
        self._clear_group_info()
        self._fire_member_list_left_event(self.zone.entity_id, group)
        self.zone.async_write_ha_state()

    def _fire_group_controller_mute_changed_event(self) -> None:
        """Notify group members the group_controller has changed mute state."""

        _LOGGER.debug(
            "GROUPING:EVENT:CONTROLLER:FIRE_GROUP_CONTROLLER_MUTE_CHANGED_EVENT From Controller:zone %d %s/group:%s/members: %s/mute: %s",
            self.zone.zone_id,
            self.zone.entity_id,
            self.group_id,
            self.group_members,
            str(self.zone.is_volume_muted),
        )

        self.zone.hass.bus.async_fire(
            SPEAKER_GROUP_CONTROLLER_MUTE_CHANGED,
            {
                "sender": self.zone.entity_id,
                "group": self.group_id,
                "mute": self.zone.is_volume_muted,
            },
        )

    def _fire_group_controller_source_changed_event(self) -> None:
        """Notify group members the group_controller has changed source."""

        _LOGGER.debug(
            "GROUPING:EVENT:CONTROLLER:FIRE_GROUP_CONTROLLER_SOURCE_CHANGED_EVENT From Controller:zone %d %s/group:%s/members: %s/source: %s",
            self.zone.zone_id,
            self.zone.entity_id,
            self.group_id,
            self.group_members,
            self.zone.source,
        )

        self.zone.hass.bus.async_fire(
            SPEAKER_GROUP_CONTROLLER_SOURCE_CHANGED,
            {
                "sender": self.zone.entity_id,
                "group": self.group_id,
                "source": self.zone.source,
            },
        )

    def _fire_group_controller_volume_changed_event(self) -> None:
        """Notify group members the group_controller has changed volume."""

        _LOGGER.debug(
            "GROUPING:EVENT:CONTROLLER:FIRE_GROUP_CONTROLLER_VOLUME_CHANGED_EVENT From Controller:zone %d %s/group:%s/members: %s/volume: %f",
            self.zone.zone_id,
            self.zone.entity_id,
            self.group_id,
            self.group_members,
            self.zone.volume_level,
        )

        self.zone.hass.bus.async_fire(
            SPEAKER_GROUP_CONTROLLER_VOLUME_CHANGED,
            {
                "sender": self.zone.entity_id,
                "group": self.group_id,
                "volume": self.zone.volume_level,
            },
        )

    def _fire_member_list_joined_event(self, group_joiner: str, group: str) -> None:
        """Notify group there's a new member."""
        self.zone.hass.bus.async_fire(
            SPEAKER_GROUP_MEMBER_LIST_JOINED,
            {
                "sender": self.zone.entity_id,
                "group": group,
                "group_joiner": group_joiner,
            },
        )

    def _fire_member_list_left_event(self, group_leaver: str, group: str) -> None:
        """Notify group a member has left."""
        self.zone.hass.bus.async_fire(
            SPEAKER_GROUP_MEMBER_LIST_LEFT,
            {
                "sender": self.zone.entity_id,
                "group": group,
                "group_leaver": group_leaver,
            },
        )

    @group_member_or_controller_check
    async def group_member_list_joined_event_cb(self, event: Event) -> None:
        """Event callback to update group_members after a member joined the group."""

        if event.data["group_joiner"] in self.group_members:
            return

        # For some reason when modifying the existing list and calling
        # async_write_ha_state, HA is not picking up the state changes to
        # group_members.  Creating a new list object for group members here fixes
        # this.
        new_member_list = self.group_members.copy()
        new_member_list.append(event.data["group_joiner"])
        self.group_members = new_member_list

        # self.group_members.append(event.data["group_joiner"])

        _LOGGER.debug(
            "GROUPING:EVENT:MEMBER_LIST_JOINER This member zone:%d %s/Controller:zone %s/group:%s/new member:%s/members:%s",
            self.zone.zone_id,
            self.zone.entity_id,
            self.group_controller,
            event.data["group"],
            event.data["group_joiner"],
            self.group_members,
        )
        self.zone.async_write_ha_state()

    @group_member_or_controller_check
    async def group_member_list_left_event_cb(self, event: Event) -> None:
        """Event callback to update group_members after a member left the group."""

        if event.data["group_leaver"] == self.group_controller:
            _LOGGER.debug(
                "GROUPING:EVENT:MEMBER_LIST_LEAVER_IS_CONTROLLER Leaving group due to controller leaving group, this member zone:%d %s/Controller:zone %s/group:%s/left member:%s/members:%s",
                self.zone.zone_id,
                self.zone.entity_id,
                self.group_controller,
                event.data["group"],
                event.data["group_leaver"],
                self.group_members,
            )
            self._clear_group_info()
            await self.zone.async_turn_off()
        else:
            # For some reason when modifying the existing list and calling
            # async_write_ha_state, HA was not picking up the state changes to
            # group_members.  Creating a new list object for group members here fixes
            # this.
            new_member_list = self.group_members.copy()
            self._remove_member_from_group_members(
                event.data["group_leaver"], new_member_list
            )
            self.group_members = new_member_list

            # self._remove_member_from_group_members(event.data["group_leaver"], self.group_members)

            if len(self.group_members) == 1:
                # This zone is the only member left in the group so leave.
                _LOGGER.debug(
                    "GROUPING:EVENT:MEMBER_LIST_LEAVER Leaving group as this zone is the only remaining member - This zone:%d %s/Controller:zone %s/group:%s/left member:%s/members:%s",
                    self.zone.zone_id,
                    self.zone.entity_id,
                    self.group_controller,
                    event.data["group"],
                    event.data["group_leaver"],
                    self.group_members,
                )
                self._clear_group_info()
                await self.zone.async_turn_off()

            else:
                _LOGGER.debug(
                    "GROUPING:EVENT:MEMBER_LIST_LEAVER This member zone:%d %s/Controller:zone %s/group:%s/left member:%s/members:%s",
                    self.zone.zone_id,
                    self.zone.entity_id,
                    self.group_controller,
                    event.data["group"],
                    event.data["group_leaver"],
                    self.group_members,
                )

        self.zone.async_write_ha_state()

    async def group_join_event_cb(self, event: Event) -> None:
        """Event callback to join this zone to a group."""

        if event.data["target_entity"] != self.zone.entity_id:
            return

        if self.zone_is_group_controller:
            # Zone is in an existing group as a controller and leaving to join a group
            # as a member

            _LOGGER.debug(
                "GROUPING:EVENT:CONTROLLER_CHANGE_GROUP_BECOME_MEMBER This (previous controller) zone: %d %s/old group:%s/new controller: %s/new group:%s/new group members:%s",
                self.zone.zone_id,
                self.zone.entity_id,
                self.group_id,
                event.data["group"],
                event.data["group_controller"],
                event.data["group_members"],
            )
            self._fire_member_list_left_event(self.zone.entity_id, self.group_id)
            self._fire_member_list_joined_event(
                self.zone.entity_id, event.data["group"]
            )

        elif self.zone_is_group_member:
            # Zone is in an existing group and joining a different group

            _LOGGER.debug(
                "GROUPING:EVENT:MEMBER_CHANGE_GROUP This zone: %d %s/old group:%s/new group:%s/new controller:%s/new group members:%s",
                self.zone.zone_id,
                self.zone.entity_id,
                self.group_id,
                event.data["group"],
                event.data["group_controller"],
                event.data["group_members"],
            )
            self._fire_member_list_left_event(self.zone.entity_id, self.group_id)
            self._fire_member_list_joined_event(
                self.zone.entity_id, event.data["group"]
            )

        elif self.zone_is_group_non_member:
            _LOGGER.debug(
                "GROUPING:EVENT:MEMBER_JOIN_NEW_GROUP This zone: %d %s/new group:%s/new controller:%s/new group members:%s",
                self.zone.zone_id,
                self.zone.entity_id,
                event.data["group"],
                event.data["group_controller"],
                event.data["group_members"],
            )
            self._fire_member_list_joined_event(
                self.zone.entity_id, event.data["group"]
            )

        self._group_status = GroupStatus.MEMBER
        self.group_id = event.data["group"]
        self.group_controller = event.data["group_controller"]
        self.group_members = event.data["group_members"]
        self.zone.async_write_ha_state()

        if self.zone.state == STATE_OFF:
            # Need to get the source and volume now rather than wait for the state
            # update event callbacks to run so the info can be included in the event
            # sent to the other zones to be grouped.
            self.zone.process_zone_status(
                await self.zone.nuvo.set_power(self.zone.zone_id, True)
            )
            # self.zone.process_zone_status(await self.zone.async_turn_on())

        sync_source = event.data["source"]
        if sync_source in self.zone.source_list and self.zone.source != sync_source:
            await self.zone.async_select_source(sync_source)

        if event.data["volume"]:
            if self.zone.is_volume_muted:
                await self.zone.async_mute_volume(False)
            await self.zone.async_set_volume_level(event.data["volume"])
        elif not self.zone.is_volume_muted:
            await self.zone.async_mute_volume(True)

    @group_member_check
    async def group_controller_mute_changed_event_cb(self, event: Event) -> None:
        """Event callback for zone to sync mute status with the group controller."""

        if self.zone.is_volume_muted != event.data["mute"]:
            _LOGGER.debug(
                "GROUPING:EVENT:MEMBER:MUTE_SYNC_WITH_CONTROLLER: This member zone:%d %s/controller %s/group:%s/mute:%s",
                self.zone.zone_id,
                self.zone.entity_id,
                self.group_controller,
                event.data["group"],
                str(event.data["mute"]),
            )
            await self.zone.async_mute_volume(event.data["mute"])
            self.zone.async_write_ha_state()

    @group_member_check
    async def group_controller_source_changed_event_cb(self, event: Event) -> None:
        """Event callback for zone to sync source with the group controller."""
        sync_source = event.data["source"]
        if sync_source in self.zone.source_list and self.zone.source != sync_source:
            _LOGGER.debug(
                "GROUPING:EVENT:MEMBER:SOURCE_SYNC_WITH_CONTROLLER: This member zone:%d %s/controller %s/group:%s/source:%s",
                self.zone.zone_id,
                self.zone.entity_id,
                self.group_controller,
                event.data["group"],
                event.data["source"],
            )
            await self.zone.async_select_source(sync_source)
            self.zone.async_write_ha_state()

    @group_member_check
    async def group_controller_volume_changed_event_cb(self, event: Event) -> None:
        """Event callback for a zone to sync its volume with the group controller."""

        if self.zone.volume_level != event.data["volume"]:
            _LOGGER.debug(
                "GROUPING:EVENT:MEMBER:VOLUME_SYNC_WITH_CONTROLLER: This member zone:%d %s/controller %s/group:%s/volume:%f",
                self.zone.zone_id,
                self.zone.entity_id,
                self.group_controller,
                event.data["group"],
                event.data["volume"],
            )
            if self.zone.is_volume_muted:
                await self.zone.async_mute_volume(False)
            await self.zone.async_set_volume_level(event.data["volume"])

            self.zone.async_write_ha_state()
