"""Free text: the plant inventory, and where a mow came from.

A PLANT SLOT IS DECLARED BY ITS NAME AND BY NOTHING ELSE. An empty name means
the slot contributes no rows at all, rather than two blank ones -- which is
what keeps a fresh install from showing sixteen phantom plant tasks. The kind,
species and notes describe a slot that already exists; they never bring one
into being.
"""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import YardConfigEntry
from .coordinator import YardCoordinator
from .entity import YardEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YardConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the yard text entities."""
    coordinator = entry.runtime_data
    entities: list[TextEntity] = [YardLastMowSourceText(coordinator)]
    for index in range(1, coordinator.plant_slots + 1):
        entities.append(YardPlantText(coordinator, index, "name", "Name", "mdi:tag"))
        entities.append(
            YardPlantText(coordinator, index, "species", "Species", "mdi:leaf")
        )
        entities.append(
            YardPlantText(coordinator, index, "notes", "Notes", "mdi:note-text")
        )
    async_add_entities(entities)


class YardPlantText(YardEntity, TextEntity):
    """One free-text field on one plant slot."""

    _attr_mode = TextMode.TEXT
    _attr_native_max = 255

    def __init__(
        self,
        coordinator: YardCoordinator,
        index: int,
        field: str,
        label: str,
        icon: str,
    ) -> None:
        """Bind to one slot and one field."""
        super().__init__(coordinator, f"plant_{index}_{field}")
        self._index = index
        self._field = field
        self._attr_icon = icon
        self._attr_name = f"Plant {index} {label}"

    @property
    def native_value(self) -> str:
        """The stored text, empty when unset.

        EMPTY STRING, NOT None. A `TextEntity` returning None reads as
        `unknown` on every surface, and `unknown` is exactly the string the
        macro this replaces had to special-case out of the declared check --
        an empty slot would have started declaring itself again.
        """
        return self.coordinator.plant_slot(self._index).get(self._field) or ""

    async def async_set_value(self, value: str) -> None:
        """Store it."""
        await self.coordinator.async_update_plant(self._index, **{self._field: value})


class YardLastMowSourceText(YardEntity, TextEntity):
    """Which entity reported the last stamped cut.

    Written by the mow-session listener, editable so a hand-recorded mow can
    say where it came from. Empty means no session has been stamped.
    """

    _attr_name = "Last Mow Source"
    _attr_icon = "mdi:robot-mower-outline"
    _attr_mode = TextMode.TEXT
    _attr_native_max = 255

    def __init__(self, coordinator: YardCoordinator) -> None:
        """Bind."""
        super().__init__(coordinator, "last_mow_source")

    @property
    def native_value(self) -> str:
        """The stored source."""
        return self.coordinator.state["mow"].get("source") or ""

    async def async_set_value(self, value: str) -> None:
        """Store it."""
        await self.coordinator.async_update_mow(source=value)
