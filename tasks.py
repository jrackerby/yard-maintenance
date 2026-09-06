"""The yard maintenance task table. One definition, many readers.

This is `custom_templates/yard_tasks.jinja` rewritten as Python (GH-605). The
rule is unchanged; only the language and the failure mode are.

WHY IT WAS A MACRO, AND WHY IT NO LONGER NEEDS TO BE. Five sensors needed the
same answer -- which tasks are due, which are overdue, which have never been
recorded, which do not apply to this lawn -- and writing that loop into each
entity's own template is the defect `packages/hvac_maintenance.yaml` still
carries: its due COUNT and its overdue_items ATTRIBUTE are two hand-copied
transcriptions of one rule and nothing makes them move together (LAW §1: a
config key read by two code paths goes through one accessor). A Jinja macro
was the only way to write it once, and a macro can only return a STRING, so
the table crossed into every reader as JSON and was parsed back.

That round trip is gone. So is the blast radius that made it dangerous: a
syntax error in the macro took every reader unavailable at once, which is why
`tools/test_yard_tasks.py` had to render the file against stubbed HA globals
on every push.

THIS MODULE IMPORTS NOTHING FROM `homeassistant`, deliberately -- the same
contract §11 puts on `household_state`'s resolver. It is a pure function of
its inputs, so the 45 table cases test it directly with no HA at all.

ROW SHAPE -- unchanged from the macro, because the board reads it verbatim off
`sensor.yard_maintenance_due_count`'s `rows` attribute:

  key         stable slug, safe to match on
  label       human string for a notification or a board
  group       lawn | turf | plant -- what kind of work it is
  last        epoch seconds of the last recorded occurrence, 0 if unset
  interval    cadence in days actually used for this row
  due         epoch seconds this becomes due, 0 when unset or suppressed
  days_left   whole days until due; negative means overdue
  unset       true when the date has never been recorded
  overdue     true only when the date IS set and the interval has passed
  suppressed  reason string when this task does not apply here, else ''

THREE STATES, NOT TWO. `unset`, `overdue` and neither are different facts
(LAW §11: `ok at zero` and `could not read` do not collapse). A task nobody
has ever recorded is NOT overdue -- there is no anchor to be late against --
but it is also not done, and a rollup reporting it green is the silent-empty
failure GH-543 records for the receptacle inventory next door. Readers surface
unset separately; they never fold it into the due count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .const import (
    COOL_SEASON,
    COOL_WINDOW,
    EPOCH_2000,
    FERTILIZER_KEY,
    FERTILIZER_LABEL,
    GROUP_LAWN,
    GROUP_PLANT,
    GROUP_TURF,
    LAWN_CADENCE,
    LAWN_LABEL,
    SUPPRESS_OVERSEED_WARM,
    TURF_ROWS,
    WARM_SEASON,
    WARM_WINDOW,
)

SEASON_COOL = "cool"
SEASON_WARM = "warm"
SEASON_UNKNOWN = "unknown"


def round_common(value: float, digits: int = 0) -> float:
    """Round the way Jinja's `round` filter does -- which is Python's `round`.

    THIS IS BANKER'S ROUNDING AND THAT IS NOT A BUG. It looks like one: the
    obvious reading of Jinja's default `method="common"` is "commercial
    rounding, half away from zero", and this function was first written that
    way. `tools/yard_port_equivalence.py` caught it on the first run -- the
    macro reports `days_left` 0 where half-away gives 1, and 2 where it gives
    3. Jinja's `do_round` simply calls the builtin for `common`, so the
    template has always rounded half-to-even.

    `days_left` lands on a .5 boundary routinely -- a task due in exactly
    twelve hours -- so this is the difference between a row appearing in the
    7-day `due_soon` window and not. Kept as a named function rather than an
    inline `round()` so the next person to think it looks wrong finds this
    note instead of "fixing" it back.
    """
    return round(value, digits)


def _as_float(value: Any, default: float = 0.0) -> float:
    """Jinja's `| float(default)` -- never raises, always lands on a number."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class PlantSlot:
    """One declared plant. A slot is DECLARED when its name is non-empty.

    An undeclared slot emits no rows at all rather than an empty pair, which
    is what keeps a fresh install from showing sixteen blank plant tasks.
    """

    index: int
    name: str = ""
    kind: str = ""
    last_pruned: float = 0.0
    last_treated: float = 0.0
    prune_interval: float = 365.0
    treat_interval: float = 365.0

    @property
    def declared(self) -> bool:
        """True when this slot names a plant.

        THE SENTINEL STRINGS ARE NOT PARANOIA. Home Assistant returns the
        literal string `unknown` for an entity that exists but has not
        restored, and `unavailable` for one whose integration is down, so the
        macro this replaces excluded both by name. Storage removes that
        failure mode going forward -- but every live `input_text.yard_plant_N
        _name` holds exactly the string `unknown` right now, and migration
        reads those helpers. Dropping the check creates sixteen phantom plant
        rows on the first run. Caught by tools/yard_port_equivalence.py.
        """
        return self.name.strip().lower() not in ("", "unknown", "unavailable", "none")


@dataclass(frozen=True, slots=True)
class YardInputs:
    """Everything the table reads, and nothing else.

    Passing state in rather than reaching for it is what lets the 45 cases run
    with no Home Assistant present.
    """

    now: float
    month: int
    day: int
    grass_type: str = ""
    dates: dict[str, float] = field(default_factory=dict)
    intervals: dict[str, float] = field(default_factory=dict)
    plants: tuple[PlantSlot, ...] = ()


def season_type(grass_type: str) -> str:
    """cool | warm | unknown.

    `Mixed` resolves to unknown DELIBERATELY. A mixed stand has no single
    renovation window, and picking the larger half would be a guess dressed as
    a schedule (LAW §14: ask rather than guess about the physical world). The
    programme sensor says so in words instead.
    """
    if grass_type in COOL_SEASON:
        return SEASON_COOL
    if grass_type in WARM_SEASON:
        return SEASON_WARM
    return SEASON_UNKNOWN


def renovation_window(grass_type: str, month: int, day: int) -> dict[str, Any]:
    """The renovation window for this lawn.

    Bounds are inclusive at both ends. `open` is whether the given date falls
    inside the aeration window; an unknown stand is never open, because a
    window nobody can name cannot be open.
    """
    season = season_type(grass_type)
    if season == SEASON_COOL:
        low, high, overseed = COOL_WINDOW[0], COOL_WINDOW[1], True
    elif season == SEASON_WARM:
        low, high, overseed = WARM_WINDOW[0], WARM_WINDOW[1], False
    else:
        low = high = None
        overseed = None

    is_open = False
    if low is not None and high is not None:
        is_open = low <= (month, day) <= high

    return {
        "season": season,
        "aerate_from": f"{low[0]:02d}-{low[1]:02d}" if low else None,
        "aerate_to": f"{high[0]:02d}-{high[1]:02d}" if high else None,
        "overseed": overseed,
        "open": is_open,
    }


def _row(
    key: str,
    label: str,
    group: str,
    last: Any,
    interval: Any,
    suppressed: str,
    now: float,
) -> dict[str, Any]:
    """One task row.

    An `interval` of 0 or less is treated as UNSET rather than as "due now".
    A non-positive cadence would otherwise make every row permanently overdue
    and fire a notification for every task in the yard at once -- LAW §10, a
    monitor whose blind spot correlates with what it monitors. This mattered
    more under the package, where an input_number that had not restored yet
    read 0; it is kept because a stored interval can still be cleared.
    """
    last_f = _as_float(last)
    interval_f = _as_float(interval)

    unset = (last_f <= EPOCH_2000) or (interval_f <= 0)
    due = 0.0 if (unset or suppressed) else last_f + interval_f * 86400

    return {
        "key": key,
        "label": label,
        "group": group,
        "last": last_f if last_f > EPOCH_2000 else 0,
        "interval": int(round_common(interval_f)),
        "due": int(round_common(due)),
        "days_left": 0 if due == 0 else int(round_common((due - now) / 86400)),
        "unset": unset and not suppressed,
        "overdue": (not unset) and (not suppressed) and (due < now),
        "suppressed": suppressed,
    }


def task_rows(inputs: YardInputs) -> list[dict[str, Any]]:
    """The whole table.

    Order is lawn programme, then turf cadence, then plants in slot order.
    Callers must not depend on it for anything but display.
    """
    season = season_type(inputs.grass_type)
    rows: list[dict[str, Any]] = []

    # Fertiliser carries an editable interval because feeding rate is the one
    # part of a programme a household genuinely re-tunes; the rest run on the
    # constants above.
    rows.append(
        _row(
            FERTILIZER_KEY,
            FERTILIZER_LABEL,
            GROUP_LAWN,
            inputs.dates.get(FERTILIZER_KEY),
            inputs.intervals.get(FERTILIZER_KEY, 60.0),
            "",
            inputs.now,
        )
    )

    for key, cadence in LAWN_CADENCE.items():
        # ONE SUPPRESSION RULE, AND IT IS STATED RATHER THAN HIDDEN (LAW §11:
        # a declined signal is stated on the entity, never silently dropped).
        # Warm-season turf spreads by stolon and rhizome and is renovated by
        # aeration alone; scattering seed over it is not part of the
        # programme, so the row is emitted, marked, and left out of the count.
        suppressed = (
            SUPPRESS_OVERSEED_WARM
            if (key == "overseed" and season == SEASON_WARM)
            else ""
        )
        rows.append(
            _row(
                key,
                LAWN_LABEL[key],
                GROUP_LAWN,
                inputs.dates.get(key),
                cadence,
                suppressed,
                inputs.now,
            )
        )

    for key, (label, fallback) in TURF_ROWS.items():
        rows.append(
            _row(
                key,
                label,
                GROUP_TURF,
                inputs.dates.get(key),
                inputs.intervals.get(key, fallback),
                "",
                inputs.now,
            )
        )

    # Two clocks each, both per-plant: a crape myrtle and a boxwood share no
    # pruning interval, and a shrub's feeding cadence is a property of the
    # shrub.
    for plant in inputs.plants:
        if not plant.declared:
            continue
        rows.append(
            _row(
                f"plant_{plant.index}_prune",
                f"Prune {plant.name}",
                GROUP_PLANT,
                plant.last_pruned,
                plant.prune_interval,
                "",
                inputs.now,
            )
        )
        rows.append(
            _row(
                f"plant_{plant.index}_treat",
                f"Treat {plant.name}",
                GROUP_PLANT,
                plant.last_treated,
                plant.treat_interval,
                "",
                inputs.now,
            )
        )

    return rows


def overdue_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows past their cadence. Never includes unset or suppressed ones."""
    return [r for r in rows if r["overdue"]]


def unset_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows with no anchor. Surfaced separately, never folded into the count."""
    return [r for r in rows if r["unset"]]


def due_soon_rows(rows: list[dict[str, Any]], days: int = 7) -> list[dict[str, Any]]:
    """Dated, un-suppressed rows landing within `days`."""
    return [
        r
        for r in rows
        if not r["overdue"]
        and not r["unset"]
        and not r["suppressed"]
        and r["days_left"] <= days
    ]


def next_task(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The soonest dated row, or None when nothing has a date yet."""
    dated = [r for r in rows if not r["unset"] and not r["suppressed"]]
    if not dated:
        return None
    return min(dated, key=lambda r: r["due"])
