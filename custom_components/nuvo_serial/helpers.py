"""Helper utils for interfacing with Nuvo multi-zone amplifier."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback

from .const import CONF_SOURCES, CONF_ZONES


@callback
def get_sources_from_dict(data: MappingProxyType[str, Any]) -> list[Any]:
    """Munge Sources."""
    sources_config = data[CONF_SOURCES]

    source_id_name = {int(index): name for index, name in sources_config.items()}

    source_name_id = {v: k for k, v in source_id_name.items()}

    source_names = sorted(source_name_id.keys(), key=lambda v: source_name_id[v])

    return [source_id_name, source_name_id, source_names]


@callback
def get_sources(config_entry: ConfigEntry) -> list[Any]:
    """Get the Nuvo Sources."""
    if CONF_SOURCES in config_entry.options:
        data = config_entry.options
    else:
        data = config_entry.data
    return get_sources_from_dict(data)


@callback
def get_zones(config_entry: ConfigEntry) -> dict[str, str]:
    """Get the Nuvo Zones."""
    if CONF_ZONES in config_entry.options:
        data = config_entry.options
    else:
        data = config_entry.data

    zone: dict[str, str] = data[CONF_ZONES]
    return zone
