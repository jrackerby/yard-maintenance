"""Every tunable number the yard carries.

THE MINIMUMS ARE NOT THE SMALLEST LEGAL VALUE. Under the package these bounds
did double duty: an `input_number` with no restored state comes up at its
`min`, so every minimum had to be a defensible cadence rather than 0 -- an
unseeded mow interval had to land on 3 days, never on 1. Stored state removes
that job, but the bounds are unchanged, because the second reason still holds:
`tasks.py` treats an interval at or below zero as UNSET rather than as due-now,
and a UI that can send 0 invites a value whose meaning is "this row silently
stops coming due".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import YardConfigEntry
from .const import (
    DEFAULT_PLANT_PRUNE_INTERVAL,
    DEFAULT_PLANT_TREAT_INTERVAL,
    FERTILIZER_KEY,
    TURF_ROWS,
)
from .coordinator import YardCoordinator
from .entity import YardEntity


@dataclass(frozen=True, kw_only=True)
class YardNumberDescription(NumberEntityDescription):
    """A number, plus where in the stored document it lives."""

    section: str
    field: str


INTERVAL_NUMBERS: tuple[YardNumberDescription, ...] = (
    YardNumberDescription(
        key="fertilizer_interval",
        name="Fertilizer Interval",
        icon="mdi:calendar-sync",
        native_unit_of_measurement="d",
        native_min_value=7,
        native_max_value=365,
        native_step=1,
        mode=NumberMode.BOX,
        section="intervals",
        field=FERTILIZER_KEY,
    ),
) + tuple(
    YardNumberDescription(
        key=f"{key}_interval",
        # Named from the key for the same reason datetime.py is: it is the
        # key, not the label, that the replaced helper ids were built from.
        name=key.replace("_", " ").title() + " Interval",
        icon="mdi:calendar-sync",
        native_unit_of_measurement="d",
        native_min_value=1,
        native_max_value=90,
        native_step=1,
        mode=NumberMode.BOX,
        section="intervals",
        field=key,
    )
    for key in TURF_ROWS
)

SIMPLE_NUMBERS: tuple[YardNumberDescription, ...] = (
    YardNumberDescription(
        key="lawn_area_sqft",
        name="Lawn Area Sqft",
        icon="mdi:ruler-square",
        native_unit_of_measurement="ft²",
        native_min_value=0,
        native_max_value=200000,
        native_step=100,
        mode=NumberMode.BOX,
        section="root",
        field="lawn_area_sqft",
    ),
    YardNumberDescription(
        key="blade_life_hours",
        name="Blade Life Hours",
        icon="mdi:saw-blade",
        native_unit_of_measurement="h",
        native_min_value=1,
        native_max_value=500,
        native_step=1,
        mode=NumberMode.BOX,
        section="blade",
        field="life_hours",
    ),
    YardNumberDescription(
        key="blade_minutes",
        name="Blade Minutes",
        icon="mdi:timer-outline",
        native_unit_of_measurement="min",
        native_min_value=0,
        native_max_value=100000,
        native_step=1,
        mode=NumberMode.BOX,
        section="blade",
        field="minutes",
    ),
    YardNumberDescription(
        key="last_mow_minutes",
        name="Last Mow Minutes",
        icon="mdi:timer-outline",
        native_unit_of_measurement="min",
        native_min_value=0,
        native_max_value=1000,
        native_step=1,
        mode=NumberMode.BOX,
        section="mow",
        field="last_minutes",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YardConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the yard numbers."""
    coordinator = entry.runtime_data
    entities: list[NumberEntity] = [
        YardNumber(coordinator, d) for d in INTERVAL_NUMBERS + SIMPLE_NUMBERS
    ]
    for index in range(1, coordinator.plant_slots + 1):
        entities.append(
            YardPlantNumber(
                coordinator, index, "prune_interval", "Prune Interval",
                DEFAULT_PLANT_PRUNE_INTERVAL,
            )
        )
        entities.append(
            YardPlantNumber(
                coordinator, index, "treat_interval", "Treat Interval",
                DEFAULT_PLANT_TREAT_INTERVAL,
            )
        )
    async_add_entities(entities)


class YardNumber(YardEntity, NumberEntity):
    """One stored number."""

    entity_description: YardNumberDescription

    def __init__(
        self, coordinator: YardCoordinator, description: YardNumberDescription
    ) -> None:
        """Bind to its slot in the document."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    def _bucket(self) -> dict[str, Any]:
        """The dict this number lives in."""
        section = self.entity_description.section
        if section == "root":
            return self.coordinator.state
        return self.coordinator.state[section]

    @property
    def native_value(self) -> float | None:
        """The stored value."""
        return self._bucket().get(self.entity_description.field)

    async def async_set_native_value(self, value: float) -> None:
        """Store it, then republish."""
        self._bucket()[self.entity_description.field] = value
        await self.coordinator.async_save()


class YardPlantNumber(YardEntity, NumberEntity):
    """One plant's prune or treat cadence.

    Per-plant rather than shared: a crape myrtle and a boxwood share no
    pruning interval, and a shrub's feeding cadence is a property of the shrub.
    """

    _attr_icon = "mdi:calendar-sync"
    _attr_native_unit_of_measurement = "d"
    _attr_native_min_value = 1
    _attr_native_max_value = 3650
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: YardCoordinator,
        index: int,
        field: str,
        label: str,
        default: float,
    ) -> None:
        """Bind to one slot and one of its two cadences."""
        super().__init__(coordinator, f"plant_{index}_{field}")
        self._index = index
        self._field = field
        self._default = default
        self._attr_name = f"Plant {index} {label}"

    @property
    def native_value(self) -> float | None:
        """The stored cadence."""
        return self.coordinator.plant_slot(self._index).get(self._field, self._default)

    async def async_set_native_value(self, value: float) -> None:
        """Store it."""
        await self.coordinator.async_update_plant(self._index, **{self._field: value})
