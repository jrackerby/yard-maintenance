"""The three yard problem signals."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import YardConfigEntry
from .const import EPOCH_2000
from .coordinator import YardCoordinator
from .entity import YardEntity
from .tasks import overdue_rows


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YardConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the yard binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            YardOverdueBinarySensor(coordinator),
            YardBladeDueBinarySensor(coordinator),
            YardRenovationWindowBinarySensor(coordinator),
        ]
    )


class YardOverdueBinarySensor(YardEntity, BinarySensorEntity):
    """Anything past its cadence."""

    entity_description = BinarySensorEntityDescription(
        key="maintenance_overdue",
        name="Maintenance Overdue",
        icon="mdi:sprout-outline",
        device_class=BinarySensorDeviceClass.PROBLEM,
    )

    def __init__(self, coordinator: YardCoordinator) -> None:
        """Bind."""
        super().__init__(coordinator, "maintenance_overdue")

    @property
    def is_on(self) -> bool:
        """True when at least one dated task has passed its interval."""
        return bool(overdue_rows(self.coordinator.rows))


class YardBladeDueBinarySensor(YardEntity, BinarySensorEntity):
    """Whether the blade has reached its rated life.

    UNSET IS NOT `off`, and this is the one entity in the component that
    overrides `available` to say so. A blade whose change was never recorded
    is not a blade that is fine: unavailable keeps it out of the overdue
    digest -- the same treatment an unset dated task gets -- while still
    reading differently from a healthy blade on any surface. `ok at
    zero` and `could not read` do not collapse.

    It fires on an UNDERCOUNT, because the hours behind it are accumulated
    only from sessions the installation observed, so it is late rather than
    early by construction. That is the argument for leaving the default life
    at the conservative end.
    """

    entity_description = BinarySensorEntityDescription(
        key="blade_due",
        name="Blade Due",
        icon="mdi:saw-blade",
        device_class=BinarySensorDeviceClass.PROBLEM,
    )

    def __init__(self, coordinator: YardCoordinator) -> None:
        """Bind."""
        super().__init__(coordinator, "blade_due")

    @property
    def _unset(self) -> bool:
        """True when no blade change has ever been recorded."""
        changed = self.coordinator.state["blade"].get("changed")
        return not changed or changed <= EPOCH_2000

    @property
    def available(self) -> bool:
        """Unavailable while the blade's change date is unrecorded.

        Deliberately overrides the base class's always-true. The base rule
        exists so a monitor cannot vanish with its subject; here there is no
        subject to vanish, only a fact nobody has supplied.
        """
        return not self._unset

    @property
    def is_on(self) -> bool:
        """True once accumulated hours reach the rated life."""
        blade = self.coordinator.state["blade"]
        hours = float(blade.get("minutes") or 0.0) / 60
        return hours >= float(blade.get("life_hours") or 0.0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The two numbers behind the verdict."""
        blade = self.coordinator.state["blade"]
        return {
            "hours": round(float(blade.get("minutes") or 0.0) / 60, 1),
            "life_hours": float(blade.get("life_hours") or 0.0),
        }


class YardRenovationWindowBinarySensor(YardEntity, BinarySensorEntity):
    """Whether today falls inside this stand's renovation window.

    Aeration and, on cool-season turf, overseeding are the two jobs with a
    real calendar window rather than an interval -- do them outside it and the
    money is spent for nothing. An unknown stand never reads open, because a
    window nobody can name cannot be open.
    """

    entity_description = BinarySensorEntityDescription(
        key="renovation_window_open",
        name="Renovation Window Open",
        icon="mdi:calendar-check-outline",
    )

    def __init__(self, coordinator: YardCoordinator) -> None:
        """Bind."""
        super().__init__(coordinator, "renovation_window_open")

    @property
    def is_on(self) -> bool:
        """True inside the aeration window, inclusive at both ends."""
        return bool(self.coordinator.window.get("open", False))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Which season resolved, and whether seed is part of the programme."""
        window = self.coordinator.window
        return {
            "season": window.get("season", "unknown"),
            "overseed_applies": window.get("overseed"),
        }
