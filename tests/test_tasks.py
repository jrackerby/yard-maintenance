"""Self-test for the yard task table. GH-605.

WHY THIS EXISTS. LAW §4: every assertion set needs a self-test proving it CAN
fail. The table is one rule read by six sensors, so a mistake in it is not one
wrong entity -- it is every yard entity wrong the same way. It is also the
exact shape that rots unnoticed: a maintenance rollup reads "0 due" on a
healthy yard and on a completely broken one alike.

So every leg gets a case that MUST produce its verdict, paired with a near-miss
that must NOT. The suppression leg and the unset-versus-overdue leg matter
most, because both of their failure modes are silent green.

WHAT THIS DOES NOT PROVE. It runs with no Home Assistant, so it says nothing
about whether the entities exist, whether the store round-trips, or what the
Navimow reports. Those need the estate (LAW §9). What it does prove is the
arithmetic and the branch coverage -- and the port's fidelity to the Jinja
macro it replaces was proved separately, before that macro was deleted, by
tools/archive/yard_port_equivalence.py.
"""

from __future__ import annotations

import datetime
import importlib.util
import sys
import types
from pathlib import Path

import pytest

# LOADED BY FILE PATH, NOT AS `yard_maintenance.tasks`, and that is an
# assertion rather than a convenience. Importing through the real package
# would execute `yard_maintenance/__init__.py`, which imports `homeassistant`
# and `voluptuous` -- so a path load is the only way this file can prove what
# its docstring claims: that the table stands up with no Home Assistant at all.
# If a `homeassistant` import ever leaks into tasks.py or const.py, this
# collection fails, loudly, here.
#
# The two modules go into a synthetic package rather than being loaded flat,
# because tasks.py imports const.py RELATIVELY (`from .const import ...`) --
# which is correct for the component and simply needs a package to resolve
# against. `yard_maintenance/__init__.py` is never touched.
_COMPONENT = Path(__file__).resolve().parents[1]
_PKG = "yard_pure"

if _PKG not in sys.modules:
    _pkg = types.ModuleType(_PKG)
    _pkg.__path__ = [str(_COMPONENT)]
    sys.modules[_PKG] = _pkg

    for _name in ("const", "tasks"):
        _spec = importlib.util.spec_from_file_location(
            f"{_PKG}.{_name}", _COMPONENT / f"{_name}.py"
        )
        _module = importlib.util.module_from_spec(_spec)
        sys.modules[f"{_PKG}.{_name}"] = _module
        _spec.loader.exec_module(_module)

EPOCH_2000 = sys.modules[f"{_PKG}.const"].EPOCH_2000
_tasks = sys.modules[f"{_PKG}.tasks"]

PlantSlot = _tasks.PlantSlot
YardInputs = _tasks.YardInputs
due_soon_rows = _tasks.due_soon_rows
next_task = _tasks.next_task
overdue_rows = _tasks.overdue_rows
renovation_window = _tasks.renovation_window
round_common = _tasks.round_common
season_type = _tasks.season_type
task_rows = _tasks.task_rows
unset_rows = _tasks.unset_rows

# Frozen clock. Every expectation is written against this instant, so a suite
# that passes today still passes in October -- a date-window test keyed on the
# real now() is a test that fails twice a year for no reason.
NOW = datetime.datetime(2026, 9, 5, 10, 0, 0, tzinfo=datetime.UTC)
NOW_TS = NOW.timestamp()
DAY = 86400.0

ALL_KEYS = (
    "fertilizer", "pre_emergent", "weed_control", "grub_control", "fungicide",
    "lime", "soil_test", "aeration", "overseed", "mow", "line_trim", "edging",
)


def days_ago(n: float) -> float:
    """Epoch seconds `n` days before the frozen clock."""
    return NOW_TS - n * DAY


def build(grass="Not set", dates=None, intervals=None, plants=(), when=NOW):
    """One yard."""
    return YardInputs(
        now=when.timestamp(),
        month=when.month,
        day=when.day,
        grass_type=grass,
        dates=dict(dates or {}),
        intervals=dict(intervals or {}),
        plants=tuple(plants),
    )


def row(rows, key):
    """One row by key, or a readable failure."""
    for r in rows:
        if r["key"] == key:
            return r
    raise AssertionError(f"no row {key!r}; keys={[r['key'] for r in rows]}")


# -- the three states ------------------------------------------------------


def test_fresh_yard_is_unset_not_overdue():
    """A task nobody recorded has no anchor to be late against.

    THE FAILURE THIS CATCHES IS SILENT GREEN, in both directions: folding
    unset into overdue cries wolf on a brand new install, and folding it into
    "fine" is the empty-inventory failure GH-543 records next door.
    """
    rows = task_rows(build())
    assert len(overdue_rows(rows)) == 0
    assert len(unset_rows(rows)) == 12
    assert row(rows, "mow")["unset"] is True
    assert row(rows, "mow")["overdue"] is False
    assert row(rows, "mow")["due"] == 0


def test_recorded_and_within_interval_is_neither():
    """The near-miss for the case above."""
    rows = task_rows(build(dates={"mow": days_ago(1)}, intervals={"mow": 7}))
    assert row(rows, "mow")["unset"] is False
    assert row(rows, "mow")["overdue"] is False


def test_recorded_and_past_interval_is_overdue():
    """The positive case for overdue."""
    rows = task_rows(build(dates={"mow": days_ago(30)}, intervals={"mow": 7}))
    assert row(rows, "mow")["overdue"] is True
    assert row(rows, "mow")["days_left"] < 0
    assert "Mow" in [r["label"] for r in overdue_rows(rows)]


# -- interval guards -------------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1, -365])
def test_non_positive_interval_reads_unset_never_due_now(bad):
    """A cleared cadence must not make every task in the yard overdue at once.

    LAW §10: a monitor whose blind spot correlates with what it monitors. If
    zero meant "due now", one bad write would fire a notification for every
    row simultaneously -- which reads exactly like a real emergency.
    """
    rows = task_rows(build(dates={"mow": days_ago(10)}, intervals={"mow": bad}))
    assert row(rows, "mow")["unset"] is True
    assert row(rows, "mow")["overdue"] is False


def test_smallest_positive_interval_still_computes():
    """The near-miss: 1 is a real cadence, not a guard value."""
    rows = task_rows(build(dates={"mow": days_ago(10)}, intervals={"mow": 1}))
    assert row(rows, "mow")["unset"] is False
    assert row(rows, "mow")["overdue"] is True


# -- suppression -----------------------------------------------------------


def test_warm_season_suppresses_overseed_with_a_reason():
    """A declined signal is STATED, never silently dropped (LAW §11)."""
    rows = task_rows(build(grass="Bermuda", dates={k: days_ago(400) for k in ALL_KEYS}))
    overseed = row(rows, "overseed")
    assert overseed["suppressed"]
    assert "aeration" in overseed["suppressed"]
    # Suppressed rows are emitted, but counted nowhere.
    assert overseed["overdue"] is False
    assert overseed["unset"] is False
    assert "Overseed" not in [r["label"] for r in overdue_rows(rows)]
    assert "Overseed" not in [r["label"] for r in unset_rows(rows)]


def test_cool_season_does_not_suppress_overseed():
    """The near-miss for suppression."""
    rows = task_rows(
        build(grass="Tall Fescue", dates={k: days_ago(400) for k in ALL_KEYS})
    )
    overseed = row(rows, "overseed")
    assert overseed["suppressed"] == ""
    assert overseed["overdue"] is True


def test_unknown_stand_does_not_suppress():
    """An unnamed stand suppresses nothing -- it cannot know to."""
    rows = task_rows(build(dates={k: days_ago(400) for k in ALL_KEYS}))
    assert row(rows, "overseed")["suppressed"] == ""


# -- plants ----------------------------------------------------------------


def test_undeclared_slots_emit_no_rows():
    """A slot is declared by its NAME and by nothing else."""
    rows = task_rows(
        build(
            plants=[
                PlantSlot(index=1, name="Boxwood"),
                PlantSlot(index=2, name=""),
                PlantSlot(index=3, name="   "),
            ]
        )
    )
    keys = [r["key"] for r in rows]
    assert "plant_1_prune" in keys
    assert "plant_2_prune" not in keys
    assert "plant_3_prune" not in keys


@pytest.mark.parametrize("sentinel", ["unknown", "unavailable", "none", "UNKNOWN"])
def test_ha_sentinel_names_are_not_declarations(sentinel):
    """`unknown` is a Home Assistant sentinel, not a plant.

    THE MIGRATION DEPENDS ON THIS. Every live input_text.yard_plant_N_name
    holds the literal string 'unknown' right now, so treating it as a name
    would create sixteen phantom plant rows on the first run. Caught
    originally by tools/archive/yard_port_equivalence.py.
    """
    rows = task_rows(build(plants=[PlantSlot(index=1, name=sentinel)]))
    assert [r for r in rows if r["group"] == "plant"] == []


def test_declared_plant_emits_two_clocks():
    """Prune and treat are separate cadences on the same plant."""
    rows = task_rows(
        build(
            plants=[
                PlantSlot(
                    index=1, name="Crape myrtle",
                    last_pruned=days_ago(400), last_treated=0.0,
                    prune_interval=365, treat_interval=365,
                )
            ]
        )
    )
    assert row(rows, "plant_1_prune")["label"] == "Prune Crape myrtle"
    assert row(rows, "plant_1_prune")["overdue"] is True
    assert row(rows, "plant_1_treat")["unset"] is True


# -- season and window -----------------------------------------------------


@pytest.mark.parametrize(
    ("grass", "expected"),
    [
        ("Tall Fescue", "cool"),
        ("Kentucky Bluegrass", "cool"),
        ("Bermuda", "warm"),
        ("Zoysia", "warm"),
        ("Not set", "unknown"),
        ("Nonsense", "unknown"),
        ("Mixed", "unknown"),
    ],
)
def test_season_type(grass, expected):
    """`Mixed` resolves to unknown deliberately -- LAW §14, never guess."""
    assert season_type(grass) == expected


@pytest.mark.parametrize(
    ("month", "day", "is_open"),
    [
        (9, 1, True),    # lower bound, inclusive
        (9, 5, True),
        (10, 15, True),  # upper bound, inclusive
        (10, 16, False),  # one day past
        (8, 31, False),   # one day before
        (1, 15, False),
    ],
)
def test_cool_window_bounds_are_inclusive(month, day, is_open):
    """Both ends count. Off-by-one here silently shortens the season."""
    assert renovation_window("Tall Fescue", month, day)["open"] is is_open


def test_unknown_stand_never_opens_a_window():
    """A window nobody can name cannot be open."""
    for month, day in [(9, 5), (5, 20), (1, 1)]:
        window = renovation_window("Mixed", month, day)
        assert window["open"] is False
        assert window["aerate_from"] is None
        assert window["overseed"] is None


def test_warm_window_differs_from_cool():
    """The near-miss: the two stands must not share a window."""
    assert renovation_window("Bermuda", 6, 1)["open"] is True
    assert renovation_window("Tall Fescue", 6, 1)["open"] is False
    assert renovation_window("Bermuda", 9, 5)["open"] is False
    assert renovation_window("Tall Fescue", 9, 5)["open"] is True


# -- rounding --------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.5, 0), (1.5, 2), (2.5, 2), (-0.5, 0), (-1.5, -2), (3.4, 3), (3.6, 4)],
)
def test_round_common_is_half_to_even(value, expected):
    """Pinned because it is NOT the obvious behaviour and was got wrong once.

    Jinja's `round` filter with its default method delegates to Python's
    builtin, which rounds half to even. The first version of this port rounded
    half away from zero and shifted rows in and out of the 7-day due_soon
    window; tools/archive/yard_port_equivalence.py caught it against the macro.
    """
    assert round_common(value) == expected


# -- rollups ---------------------------------------------------------------


def test_due_soon_excludes_overdue_unset_and_suppressed():
    """due_soon is the *upcoming* window, not a catch-all."""
    rows = task_rows(
        build(
            grass="Bermuda",
            dates={
                "mow": days_ago(6),        # due in 1 day, at interval 7
                "fertilizer": days_ago(400),  # overdue
                "overseed": days_ago(400),    # suppressed on warm turf
            },
            intervals={"mow": 7},
        )
    )
    labels = [r["label"] for r in due_soon_rows(rows)]
    assert "Mow" in labels
    assert "Fertilise lawn" not in labels
    assert "Overseed" not in labels
    assert "Line trim" not in labels  # unset


def test_next_task_is_the_soonest_dated_row():
    """Unset and suppressed rows can never be the next task."""
    rows = task_rows(
        build(
            dates={"mow": days_ago(6), "edging": days_ago(1)},
            intervals={"mow": 7, "edging": 21},
        )
    )
    assert next_task(rows)["key"] == "mow"


def test_next_task_is_none_on_a_fresh_yard():
    """Nothing has a date, so nothing is next -- not an arbitrary first row."""
    assert next_task(task_rows(build())) is None


def test_sentinel_dates_read_as_unset():
    """Anything at or below 2000-01-01 is the never-recorded floor."""
    for stamp in (0.0, 1.0, EPOCH_2000):
        rows = task_rows(build(dates={"mow": stamp}, intervals={"mow": 7}))
        assert row(rows, "mow")["unset"] is True
        assert row(rows, "mow")["last"] == 0


def test_the_suite_can_fail():
    """LAW §4: an assertion set that cannot fail is not a gate.

    Proves the table actually responds to its inputs rather than returning a
    constant that happens to satisfy everything above.
    """
    fresh = task_rows(build())
    stale = task_rows(build(dates={k: days_ago(4000) for k in ALL_KEYS}))
    assert len(overdue_rows(fresh)) == 0
    assert len(overdue_rows(stale)) == 12
    assert fresh != stale
