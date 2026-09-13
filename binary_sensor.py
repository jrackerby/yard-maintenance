"""The three yard problem signals, and the two stand-off signals."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.util import dt as dt_util

from . import YardConfigEntry
from .const import (
    EPOCH_2000,
    HOLD_CHEMICAL,
    HOLD_MAINTENANCE,
    HOLD_TARGET_IRRIGATION,
    HOLD_TARGET_MOW,
)
from .coordinator import YardCoordinator
from .entity import YardEntity
from .tasks import active_holds, hold_until, overdue_rows


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
            YardMowingHoldBinarySensor(coordinator),
            YardIrrigationHoldBinarySensor(coordinator),
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


class YardHoldBinarySensor(YardEntity, BinarySensorEntity):
    """`on` while a logged task is holding one consumer off the lawn.

    ONE ENTITY PER CONSUMER, NOT ONE PER HOLD. An irrigation controller asks
    "may I water?" and a robotic mower asks "may I cut?"; each wants a single
    boolean it can gate on, and the same application answers the two
    differently (a granular feed wants water and no mower). Which holds are
    behind the answer, and of what kind, are attributes -- the row a surface
    names is the one that lifts LAST, because that is the one the consumer is
    actually waiting on.

    NO DEVICE CLASS, DELIBERATELY. `problem` would paint a correctly observed
    stand-off as a fault; a hold is the yard working as recorded.

    Never unavailable. A yard with no logged applications has no holds, which
    is `off` and a fact, not an unknown.
    """

    _target: str

    def __init__(self, coordinator: YardCoordinator, key: str) -> None:
        """Bind."""
        super().__init__(coordinator, key)

    @property
    def _active(self) -> list[dict[str, Any]]:
        return active_holds(self.coordinator.holds, self._target)

    @property
    def is_on(self) -> bool:
        """True while any hold on this consumer is inside its window."""
        return bool(self._active)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Which holds, of what kind, and when the last of them lifts.

        `until` is ISO-8601 in the installation's timezone or None, so a
        controller's own "delay until" field can take it as-is. `holds` is
        the active rows only, longest-remaining first.
        """
        active = self._active
        until = hold_until(self.coordinator.holds, self._target)
        until_field = f"{self._target}_until"
        return {
            "holds": [
                {
                    "key": r["key"],
                    "label": r["label"],
                    "kind": r["kind"],
                    "since": dt_util.as_local(
                        dt_util.utc_from_timestamp(r["since"])
                    ).isoformat(),
                    "until": dt_util.as_local(
                        dt_util.utc_from_timestamp(r[until_field])
                    ).isoformat(),
                }
                for r in active
            ],
            "reason": active[0]["label"] if active else None,
            "until": (
                dt_util.as_local(dt_util.utc_from_timestamp(until)).isoformat()
                if until
                else None
            ),
            "chemical": any(r["kind"] == HOLD_CHEMICAL for r in active),
            "maintenance": any(r["kind"] == HOLD_MAINTENANCE for r in active),
        }


class YardMowingHoldBinarySensor(YardHoldBinarySensor):
    """Whether a robotic mower should stay docked."""

    _target = HOLD_TARGET_MOW
    entity_description = BinarySensorEntityDescription(
        key="mowing_hold",
        name="Mowing Hold",
        icon="mdi:robot-mower",
    )

    def __init__(self, coordinator: YardCoordinator) -> None:
        """Bind."""
        super().__init__(coordinator, "mowing_hold")


class YardIrrigationHoldBinarySensor(YardHoldBinarySensor):
    """Whether an irrigation controller should skip its next run."""

    _target = HOLD_TARGET_IRRIGATION
    entity_description = BinarySensorEntityDescription(
        key="irrigation_hold",
        name="Irrigation Hold",
        icon="mdi:water-off",
    )

    def __init__(self, coordinator: YardCoordinator) -> None:
        """Bind."""
        super().__init__(coordinator, "irrigation_hold")
