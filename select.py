"""The grass stand, and what each plant slot is.

FIRST OPTION IS THE UNSET ONE, AND THAT IS STILL LOAD-BEARING. Under the
package it was structural: an `input_select` with no restored state comes up on
its first option, so `Not set` had to lead or a fresh install would assert a
grass type nobody chose. Stored state removes that mechanism, but the option
stays, because "nobody has told this yard what it is" remains a real answer and
the alternative is a picker with no way to express it.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import YardConfigEntry
from .const import (
    GRASS_OPTIONS,
    GRASS_UNSET,
    PLANT_KIND_OPTIONS,
    PLANT_KIND_UNSET,
)
from .coordinator import YardCoordinator
from .entity import YardEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YardConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the yard selects."""
    coordinator = entry.runtime_data
    entities: list[SelectEntity] = [YardGrassTypeSelect(coordinator)]
    entities.extend(
        YardPlantKindSelect(coordinator, i)
        for i in range(1, coordinator.plant_slots + 1)
    )
    async_add_entities(entities)


class YardGrassTypeSelect(YardEntity, SelectEntity):
    """Which stand this lawn is.

    The single most consequential value in the component: it decides the
    renovation window, whether overseeding is part of the programme at all,
    and therefore what `sensor.yard_grass_program` says. `Mixed` resolves to
    unknown deliberately -- a mixed stand has no single renovation window, and
    picking the larger half would be a guess dressed as a schedule.
    """

    _attr_name = "Grass Type"
    _attr_icon = "mdi:grass"
    _attr_options = list(GRASS_OPTIONS)

    def __init__(self, coordinator: YardCoordinator) -> None:
        """Bind."""
        super().__init__(coordinator, "grass_type")

    @property
    def current_option(self) -> str:
        """The stored stand type."""
        return self.coordinator.state.get("grass_type") or GRASS_UNSET

    async def async_select_option(self, option: str) -> None:
        """Set the stand type."""
        await self.coordinator.async_set_grass(option)


class YardPlantKindSelect(YardEntity, SelectEntity):
    """What one plant slot holds."""

    _attr_icon = "mdi:tree-outline"
    _attr_options = list(PLANT_KIND_OPTIONS)

    def __init__(self, coordinator: YardCoordinator, index: int) -> None:
        """Bind to one slot."""
        super().__init__(coordinator, f"plant_{index}_kind")
        self._index = index
        self._attr_name = f"Plant {index} Kind"

    @property
    def current_option(self) -> str:
        """The stored kind."""
        return self.coordinator.plant_slot(self._index).get("kind") or PLANT_KIND_UNSET

    async def async_select_option(self, option: str) -> None:
        """Set the kind."""
        await self.coordinator.async_update_plant(self._index, kind=option)
