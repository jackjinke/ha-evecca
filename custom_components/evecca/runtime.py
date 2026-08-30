"""Typed config-entry runtime data for EVECCA."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import EveccaCoordinator
    from .mqtt import EveccaMqttClient


@dataclass(slots=True)
class EveccaRuntimeData:
    """Runtime objects for one configured EVECCA family."""

    coordinator: EveccaCoordinator
    mqtt: EveccaMqttClient


type EveccaConfigEntry = ConfigEntry[EveccaRuntimeData]
