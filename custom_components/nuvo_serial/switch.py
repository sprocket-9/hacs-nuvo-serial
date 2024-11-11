"""Support for interfacing with Nuvo multi-zone amplifier."""

from __future__ import annotations

import logging
from typing import Any

from nuvo_serial.const import (
    SOURCE_CONFIGURATION,
    SYSTEM_MUTE,
    SYSTEM_PAGING,
    ZONE_EQ_STATUS,
    ZONE_VOLUME_CONFIGURATION,
)
from nuvo_serial.grand_concerto_essentia_g import NuvoAsync
from nuvo_serial.message import Mute, Paging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PORT, CONF_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONTROL_EQ_LOUDCMP,
    CONTROL_NUVONET_SOURCE,
    CONTROL_VOLUME_RESET,
    DOMAIN,
    NUVO_OBJECT,
    SOURCE,
    ZONE,
)
from .helpers import get_sources, get_zones
from .nuvo_control import NuvoControl

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Switch entities associated with each Nuvo multi-zone amplifier zone."""

    model = config_entry.data[CONF_TYPE]
    nuvo = hass.data[DOMAIN][config_entry.entry_id][NUVO_OBJECT]
    port = config_entry.data[CONF_PORT]
    zones = get_zones(config_entry)
    sources = get_sources(config_entry)[0]
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

    for source_id, source_name in sources.items():
        s_id = int(source_id)
        entities.append(
            NuvonetSource(
                nuvo=nuvo,
                model=model,
                namespace=config_entry.entry_id,
                nuvo_id=s_id,
                nuvo_entity_type=SOURCE,
                nuvo_entity_name=source_name,
                control_name=CONTROL_NUVONET_SOURCE,
                nuvo_config_key=CONTROL_NUVONET_SOURCE,
                nuvo_msg_class=SOURCE_CONFIGURATION,
                port=port,
            )
        )

    entities.append(PageSwitch(nuvo, port, config_entry.entry_id, "page"))
    entities.append(MuteSwitch(nuvo, port, config_entry.entry_id, "mute"))

    async_add_entities(entities, False)


class NuvoSwitchControl(NuvoControl, SwitchEntity):
    """Nuvo Switch based control."""

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device info for this device."""
        return DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    f"{self._namespace}_{self._nuvo_entity_type}_{self._nuvo_id}_{self._nuvo_entity_type}",
                )
            },
            manufacturer="Nuvo",
            model=self._nuvo_entity_type.capitalize(),
            name=self._nuvo_entity_name.capitalize(),
        )

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
        return "Volume reset"

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


class NuvonetSource(NuvoSwitchControl):
    """Nuvonet source status control for Nuvo amplifier source."""

    @property
    def is_on(self) -> bool:
        """Return True if entity is on."""
        return bool(self._control_value)

    @property
    def name(self) -> str:
        """Return the name of the control."""
        return "Nuvonet Source"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self._nuvo.set_source_nuvonet(self._nuvo_id, nuvonet=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self._nuvo.set_source_nuvonet(self._nuvo_id, nuvonet=False)

    async def async_toggle(self, **kwargs: Any) -> None:
        """Toggle the entity."""
        await self._nuvo.set_source_nuvonet(self._nuvo_id, not (self.is_on))

    async def _nuvo_get_control_value(self) -> None:
        """Get value."""
        await self._nuvo.source_configuration(self._nuvo_id)


class PageSwitch(SwitchEntity):
    """Represention of the Nuvo system Page status."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, nuvo: NuvoAsync, port: str, namespace: str, name: str) -> None:
        """Initialize new switch."""
        self._nuvo = nuvo
        self._port = port
        self._namespace = namespace
        self._name = name
        self._state: bool | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._port)},
        )

    @property
    def unique_id(self) -> str | None:
        """Return unique ID for this device."""
        return f"{self._namespace}_{'_'.join(self._name.split())}"

    @property
    def name(self) -> str | None:
        """Return the name of the switch."""
        return self._name.capitalize()

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to register.

        Subscribe callback to handle updates from the Nuvo.
        Request initial entity state, letting the update callback handle setting it.
        """

        self._nuvo.add_subscriber(self._update_callback, SYSTEM_PAGING)

        # There's no way to query Page status so assert it.
        await self.async_turn_off()

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity is removed to register.

        Remove Nuvo update callback.
        """
        self._nuvo.remove_subscriber(self._update_callback, SYSTEM_PAGING)
        self._nuvo = None

    async def _update_callback(self, message: Paging) -> None:
        """Update entity state callback.

        Nuvo lib calls this when it receives new messages.
        """
        self._state = message["event"].page
        self.async_schedule_update_ha_state()

    @property
    def is_on(self) -> bool:
        """Return True if entity is on."""
        return bool(self._state)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self._nuvo.set_page(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self._nuvo.set_page(False)


class MuteSwitch(SwitchEntity):
    """Represention of the Nuvo system Mute status."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, nuvo: NuvoAsync, port: str, namespace: str, name: str) -> None:
        """Initialize new switch."""
        self._nuvo = nuvo
        self._port = port
        self._namespace = namespace
        self._name = name
        self._state: bool | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._port)},
        )

    @property
    def unique_id(self) -> str | None:
        """Return unique ID for this device."""
        return f"{self._namespace}_{'_'.join(self._name.split())}"

    @property
    def name(self) -> str | None:
        """Return the name of the switch."""
        return self._name.capitalize()

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to register.

        Subscribe callback to handle updates from the Nuvo.
        Request initial entity state, letting the update callback handle setting it.
        """

        self._nuvo.add_subscriber(self._update_callback, SYSTEM_MUTE)

        # There's no way to query Mute status so assert it.
        await self.async_turn_off()

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity is removed to register.

        Remove Nuvo update callback.
        """
        self._nuvo.remove_subscriber(self._update_callback, SYSTEM_MUTE)
        self._nuvo = None

    async def _update_callback(self, message: Mute) -> None:
        """Update entity state callback.

        Nuvo lib calls this when it receives new messages.
        """
        self._state = message["event"].mute
        self.async_schedule_update_ha_state()

    @property
    def is_on(self) -> bool:
        """Return True if entity is on."""
        return bool(self._state)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self._nuvo.mute_all_zones(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self._nuvo.mute_all_zones(False)
