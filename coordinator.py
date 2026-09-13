"""Stored yard state, and the coordinator that derives the table from it.

UNLIKE EVERY OTHER COMPONENT IN THIS ESTATE, THERE IS NO SERVICE TO POLL. The
state IS the integration: 87 `input_*` helpers became this file. So
the coordinator's `_async_update_data` reads nothing external -- it recomputes
the derived table from stored state and the clock, which is the only thing
that moves on its own.

THAT IS WHY THE REFRESH IS HOURLY AND NOT FASTER. Nothing here changes without
either a write (which refreshes immediately) or the passage of time, and the
table's finest unit is a whole day. A minute-by-minute poll would recompute an
identical table 1,440 times to move `days_left` once. the rule about
`appropriate-polling` wants the cadence justified by the payload, and the
payload's own resolution is a day.

THE ONE EXCEPTION IS A HOLD BOUNDARY. Holds are measured in hours, and a
controller reading `binary_sensor.yard_irrigation_hold` an hour late waters
an hour late. So after every recompute the coordinator books ONE point-in-time
refresh at the next instant any hold can change truth value, and none when
nothing is held. That is still zero polling: the timer fires exactly when the
payload moves, and the hourly tick stays as it is for everything else.

STORAGE, NOT RESTORESTATE. `RestoreEntity` would put each value back on its
own entity, which is how the helpers worked and is exactly the shape that made
them fragile: 87 independent restores with no way to validate them as a set,
and no single place to migrate. One `Store` holds the yard as one document.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    DATED_KEYS,
    DEFAULT_BLADE_LIFE_HOURS,
    DEFAULT_FERTILIZER_INTERVAL,
    DEFAULT_PLANT_PRUNE_INTERVAL,
    DEFAULT_PLANT_TREAT_INTERVAL,
    DOMAIN,
    FERTILIZER_KEY,
    GRASS_UNSET,
    PLANT_KIND_UNSET,
    STORAGE_KEY,
    STORAGE_VERSION,
    TURF_ROWS,
)
from .tasks import (
    PlantSlot,
    YardInputs,
    hold_rows,
    next_hold_boundary,
    renovation_window,
    task_rows,
)

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(hours=1)


def default_state(plant_slots: int) -> dict[str, Any]:
    """A yard nobody has recorded anything in.

    EVERY DATE IS None, AND THAT IS THE WHOLE POINT OF THE REWRITE. The
    package could not express this: a fresh `input_datetime` comes up seeded to
    TODAY, so an untouched yard asserted that all twelve jobs had just been
    done, and `script.yard_seed_helpers` existed solely to write a 1970
    sentinel over them once -- destroying real history if it ever ran twice.
    A stored None is unambiguous, needs no one-shot, and cannot be run twice.
    """
    return {
        "grass_type": GRASS_UNSET,
        "lawn_area_sqft": 0.0,
        "dates": {key: None for key in DATED_KEYS},
        "intervals": {
            FERTILIZER_KEY: DEFAULT_FERTILIZER_INTERVAL,
            **{key: float(default) for key, (_, default) in TURF_ROWS.items()},
        },
        "blade": {
            "changed": None,
            "minutes": 0.0,
            "life_hours": DEFAULT_BLADE_LIFE_HOURS,
        },
        "mow": {
            "started": None,
            "last_minutes": 0.0,
            "sessions": 0,
            "source": "",
        },
        "plants": {
            str(i): {
                "name": "",
                "species": "",
                "notes": "",
                "kind": PLANT_KIND_UNSET,
                "last_pruned": None,
                "last_treated": None,
                "prune_interval": DEFAULT_PLANT_PRUNE_INTERVAL,
                "treat_interval": DEFAULT_PLANT_TREAT_INTERVAL,
            }
            for i in range(1, plant_slots + 1)
        },
    }


def _merge_defaults(stored: dict[str, Any], plant_slots: int) -> dict[str, Any]:
    """Fill in anything a stored document predates.

    A NEW KEY MUST NOT READ AS A CLEARED ONE. Adding a task or a plant slot in
    a later version leaves older documents without it, and a `.get()` returning
    None at the read site would render that as "never recorded" -- which is
    honest for a date and a lie for an interval, where it would mean the row
    silently stops coming due. Merging once here keeps every read site simple
    and keeps the failure visible at exactly one place.
    """
    base = default_state(plant_slots)
    if not stored:
        return base

    base["grass_type"] = stored.get("grass_type", base["grass_type"])
    base["lawn_area_sqft"] = stored.get("lawn_area_sqft", base["lawn_area_sqft"])
    base["dates"].update(
        {k: v for k, v in (stored.get("dates") or {}).items() if k in base["dates"]}
    )
    base["intervals"].update(
        {
            k: v
            for k, v in (stored.get("intervals") or {}).items()
            if k in base["intervals"] and v is not None
        }
    )
    base["blade"].update(
        {k: v for k, v in (stored.get("blade") or {}).items() if k in base["blade"]}
    )
    base["mow"].update(
        {k: v for k, v in (stored.get("mow") or {}).items() if k in base["mow"]}
    )
    for index, slot in (stored.get("plants") or {}).items():
        if index in base["plants"]:
            base["plants"][index].update(
                {k: v for k, v in slot.items() if k in base["plants"][index]}
            )
    return base


class YardCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Owns the yard document and publishes the derived table."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, plant_slots: int
    ) -> None:
        """Bind the store; the document itself is loaded in `async_load`."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.plant_slots = plant_slots
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry_id}"
        )
        self.state: dict[str, Any] = default_state(plant_slots)
        self._unsub_hold_boundary: CALLBACK_TYPE | None = None

    async def async_load(self) -> None:
        """Read the document, or start a fresh one."""
        stored = await self._store.async_load()
        self.state = _merge_defaults(stored or {}, self.plant_slots)

    async def async_save(self) -> None:
        """Persist and republish, in that order.

        Persist FIRST. A refresh that publishes a value the store has not
        accepted yet is a surface disagreeing with the record it claims to
        show, and a restart resolves it by silently reverting what the user
        just did.
        """
        await self._store.async_save(self.state)
        await self.async_refresh()

    # -- mutation ---------------------------------------------------------
    # Every writer goes through one of these rather than reaching into
    # `self.state`, so persistence and republication cannot be forgotten at a
    # call site (a key read by two code paths goes through one
    # accessor -- the same applies to writes).

    async def async_set_date(self, key: str, value: float | None) -> None:
        """Record (or clear) when a dated task last happened."""
        self.state["dates"][key] = value
        await self.async_save()

    async def async_set_interval(self, key: str, days: float) -> None:
        """Set a task's cadence in days."""
        self.state["intervals"][key] = days
        await self.async_save()

    async def async_set_grass(self, grass_type: str) -> None:
        """Set the stand type, which drives the whole renovation window."""
        self.state["grass_type"] = grass_type
        await self.async_save()

    async def async_set_lawn_area(self, sqft: float) -> None:
        """Set the lawn's area."""
        self.state["lawn_area_sqft"] = sqft
        await self.async_save()

    async def async_update_blade(self, **fields: Any) -> None:
        """Patch the blade record."""
        self.state["blade"].update(fields)
        await self.async_save()

    async def async_update_mow(self, **fields: Any) -> None:
        """Patch the mow ledger."""
        self.state["mow"].update(fields)
        await self.async_save()

    async def async_update_plant(self, index: int, **fields: Any) -> None:
        """Patch one plant slot."""
        self.state["plants"][str(index)].update(fields)
        await self.async_save()

    # -- derivation -------------------------------------------------------

    def plant_slot(self, index: int) -> dict[str, Any]:
        """One plant slot's stored record."""
        return self.state["plants"][str(index)]

    def _inputs(self) -> YardInputs:
        """Everything `tasks.py` needs, and nothing it does not."""
        now = dt_util.now()
        plants = tuple(
            PlantSlot(
                index=i,
                name=self.plant_slot(i).get("name") or "",
                kind=self.plant_slot(i).get("kind") or PLANT_KIND_UNSET,
                last_pruned=self.plant_slot(i).get("last_pruned") or 0.0,
                last_treated=self.plant_slot(i).get("last_treated") or 0.0,
                prune_interval=self.plant_slot(i).get("prune_interval") or 0.0,
                treat_interval=self.plant_slot(i).get("treat_interval") or 0.0,
            )
            for i in range(1, self.plant_slots + 1)
        )
        return YardInputs(
            now=now.timestamp(),
            month=now.month,
            day=now.day,
            grass_type=self.state["grass_type"],
            dates={k: (v or 0.0) for k, v in self.state["dates"].items()},
            intervals=dict(self.state["intervals"]),
            plants=plants,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Recompute the table.

        NEVER RAISES `UpdateFailed`. There is no service to lose -- the rule about
        contract, and the note that `entity-unavailable` governs a
        coordinator with something to be unavailable FROM, which this is not.
        A yard tracker that vanishes when it cannot compute is the monitor
        whose blind spot correlates with what it monitors.
        """
        inputs = self._inputs()
        holds = hold_rows(inputs)
        self._schedule_hold_boundary(next_hold_boundary(holds, inputs.now))
        return {
            "rows": task_rows(inputs),
            "window": renovation_window(inputs.grass_type, inputs.month, inputs.day),
            "holds": holds,
        }

    def _schedule_hold_boundary(self, at: float | None) -> None:
        """Book (or cancel) the one refresh a hold edge needs.

        Always cancels the previous booking first: a recompute triggered by a
        write may have moved the edge, and two timers for one edge are one
        redundant recompute and one stale one.
        """
        if self._unsub_hold_boundary is not None:
            self._unsub_hold_boundary()
            self._unsub_hold_boundary = None
        if at is None:
            return

        async def _async_at_boundary(_now: Any) -> None:
            self._unsub_hold_boundary = None
            await self.async_refresh()

        self._unsub_hold_boundary = async_track_point_in_utc_time(
            self.hass, _async_at_boundary, dt_util.utc_from_timestamp(at)
        )

    async def async_shutdown(self) -> None:
        """Drop the hold timer along with the hourly one."""
        self._schedule_hold_boundary(None)
        await super().async_shutdown()

    @property
    def rows(self) -> list[dict[str, Any]]:
        """The task table as last derived."""
        return (self.data or {}).get("rows", [])

    @property
    def window(self) -> dict[str, Any]:
        """The renovation window as last derived."""
        return (self.data or {}).get("window", {})

    @property
    def holds(self) -> list[dict[str, Any]]:
        """The hold table as last derived, active rows and lapsed ones alike."""
        return (self.data or {}).get("holds", [])
