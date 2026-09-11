"""The read-only half of the yard: five sensors the board already reads.

EVERY STATE AND EVERY ATTRIBUTE HERE IS A CONTRACT WITH `apps/yard-maintenance`
AND MUST NOT DRIFT. The board reads five ids and, on the due-count sensor, the
whole `rows` table verbatim. `tools/yard_port_equivalence.py` proved the table
itself matches the macro; these classes are the second half of that promise --
the wrapping the board destructures.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import YardConfigEntry
from .const import BLADE_BASIS, EPOCH_2000, GROUP_PLANT
from .coordinator import YardCoordinator
from .entity import YardEntity
from .tasks import (
    due_soon_rows,
    next_task,
    overdue_rows,
    round_common,
    unset_rows,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YardConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the yard sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            YardDueCountSensor(coordinator),
            YardNextTaskSensor(coordinator),
            YardDaysSinceMowSensor(coordinator),
            YardBladeHoursSensor(coordinator),
            YardGrassProgramSensor(coordinator),
            YardMowSessionsSensor(coordinator),
        ]
    )


class YardMowSessionsSensor(YardEntity, SensorEntity):
    """How many mow sessions this ledger has closed.

    REPLACES `counter.yard_mow_sessions`, which the board reads directly. An
    integration cannot create a `counter`, so this is the one read-only id in
    the migration that does move, and the board carries it.

    TOTAL_INCREASING, not MEASUREMENT: it only ever goes up, except on a
    deliberate reset, and telling the recorder that is what makes a
    long-run statistic of it meaningful rather than noise.
    """

    entity_description = SensorEntityDescription(
        key="mow_sessions",
        name="Mow Sessions",
        icon="mdi:counter",
        native_unit_of_measurement="sessions",
        state_class=SensorStateClass.TOTAL_INCREASING,
    )

    def __init__(self, coordinator: YardCoordinator) -> None:
        """Bind."""
        super().__init__(coordinator, "mow_sessions")

    @property
    def native_value(self) -> int:
        """Closed sessions."""
        return int(self.coordinator.state["mow"].get("sessions") or 0)


class YardDueCountSensor(YardEntity, SensorEntity):
    """How many tasks are past their cadence.

    UNSET IS NEVER FOLDED IN. A task nobody has ever recorded is not overdue --
    there is no anchor to be late against -- so it is counted nowhere and
    listed separately in `unset_items`. A rollup that reported it as green
    would be the silent-empty failure this whole tracker exists to refuse.
    """

    entity_description = SensorEntityDescription(
        key="maintenance_due_count",
        name="Maintenance Due Count",
        icon="mdi:sprout",
        native_unit_of_measurement="tasks",
        state_class=SensorStateClass.MEASUREMENT,
    )

    def __init__(self, coordinator: YardCoordinator) -> None:
        """Bind."""
        super().__init__(coordinator, "maintenance_due_count")

    @property
    def native_value(self) -> int:
        """The overdue count."""
        return len(overdue_rows(self.coordinator.rows))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Everything the board destructures off this one entity."""
        rows = self.coordinator.rows
        return {
            "overdue_items": [r["label"] for r in overdue_rows(rows)],
            "unset_items": [r["label"] for r in unset_rows(rows)],
            "due_soon": [r["label"] for r in due_soon_rows(rows)],
            # A declined signal is STATED on the entity, never silently
            # dropped, and it is stated with its reason attached.
            "suppressed": [
                f"{r['label']} — {r['suppressed']}" for r in rows if r["suppressed"]
            ],
            "plants_declared": len(
                [r for r in rows if r["group"] == GROUP_PLANT]
            )
            // 2,
            "rows": rows,
        }


class YardNextTaskSensor(YardEntity, SensorEntity):
    """The soonest dated task."""

    entity_description = SensorEntityDescription(
        key="next_task",
        name="Next Task",
        icon="mdi:calendar-clock",
    )

    def __init__(self, coordinator: YardCoordinator) -> None:
        """Bind."""
        super().__init__(coordinator, "next_task")

    @property
    def native_value(self) -> str:
        """The label, or 'unknown' when nothing has a date yet."""
        row = next_task(self.coordinator.rows)
        return row["label"] if row else "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Which task, and how long."""
        row = next_task(self.coordinator.rows)
        return {
            "due_in_days": row["days_left"] if row else None,
            "key": row["key"] if row else None,
            "group": row["group"] if row else None,
        }


class YardDaysSinceMowSensor(YardEntity, SensorEntity):
    """Days since the last recorded cut."""

    entity_description = SensorEntityDescription(
        key="days_since_mow",
        name="Days Since Mow",
        icon="mdi:robot-mower-outline",
        native_unit_of_measurement="d",
        state_class=SensorStateClass.MEASUREMENT,
    )

    def __init__(self, coordinator: YardCoordinator) -> None:
        """Bind."""
        super().__init__(coordinator, "days_since_mow")

    @property
    def native_value(self) -> int | None:
        """None when no mow has ever been recorded -- not zero.

        Zero would read as "mowed today" on every surface.
        """
        last = self.coordinator.state["dates"].get("mow")
        if not last or last <= EPOCH_2000:
            return None
        return int(round_common((dt_util.utcnow().timestamp() - last) / 86400))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The mow ledger's own bookkeeping."""
        mow = self.coordinator.state["mow"]
        started = mow.get("started")
        return {
            "source": mow.get("source", ""),
            "last_session_minutes": int(mow.get("last_minutes") or 0),
            "sessions_counted": int(mow.get("sessions") or 0),
            # A session is open when the start stamp is a real time rather
            # than the sentinel -- the interlock the mow automations turn on.
            "session_open": bool(started and started > EPOCH_2000),
        }


class YardBladeHoursSensor(YardEntity, SensorEntity):
    """Blade hours accumulated since the last change.

    DERIVED HERE BECAUSE THE CLOUD WILL NOT GIVE IT. Segway keeps
    blade wear in the phone app and exposes none of it, so this is accumulated
    from the sessions this ledger closes and is a FLOOR, never a total -- any
    cut the installation did not observe is missing from it. Stated on the entity as
    `basis` rather than left for a reader to assume.
    """

    entity_description = SensorEntityDescription(
        key="blade_hours",
        name="Blade Hours",
        icon="mdi:saw-blade",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.MEASUREMENT,
    )

    def __init__(self, coordinator: YardCoordinator) -> None:
        """Bind."""
        super().__init__(coordinator, "blade_hours")

    @property
    def _unset(self) -> bool:
        """True when no blade change has ever been recorded."""
        changed = self.coordinator.state["blade"].get("changed")
        return not changed or changed <= EPOCH_2000

    @property
    def native_value(self) -> float | None:
        """Hours on the current blade, or None when the change is unrecorded."""
        if self._unset:
            return None
        minutes = float(self.coordinator.state["blade"].get("minutes") or 0.0)
        return round_common(minutes / 60, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Life, remaining, percent used -- all None while the blade is unset."""
        blade = self.coordinator.state["blade"]
        life = float(blade.get("life_hours") or 0.0)
        minutes = float(blade.get("minutes") or 0.0)
        hours = minutes / 60
        changed = blade.get("changed")

        return {
            "life_hours": life,
            "hours_remaining": None if self._unset else round_common(life - hours, 1),
            "pct_used": (
                None
                if (self._unset or life <= 0)
                else int(round_common(100 * hours / life))
            ),
            "changed": (
                None
                if self._unset
                else dt_util.utc_from_timestamp(changed).strftime("%Y-%m-%d")
            ),
            "unset": self._unset,
            "basis": BLADE_BASIS,
        }


class YardGrassProgramSensor(YardEntity, SensorEntity):
    """The stand type, and the programme that follows from it."""

    entity_description = SensorEntityDescription(
        key="grass_program",
        name="Grass Program",
        icon="mdi:grass",
    )

    def __init__(self, coordinator: YardCoordinator) -> None:
        """Bind."""
        super().__init__(coordinator, "grass_program")

    @property
    def native_value(self) -> str:
        """cool | warm | unknown."""
        return self.coordinator.window.get("season", "unknown")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The window, and the cadence table minus the plants."""
        window = self.coordinator.window
        return {
            "grass_type": self.coordinator.state["grass_type"],
            "aerate_from": window.get("aerate_from"),
            "aerate_to": window.get("aerate_to"),
            "overseed_applies": window.get("overseed"),
            "window_open": window.get("open", False),
            "program": [
                {
                    "task": r["label"],
                    "every_days": r["interval"],
                    "applies": not r["suppressed"],
                }
                for r in self.coordinator.rows
                if r["group"] != GROUP_PLANT
            ],
        }
