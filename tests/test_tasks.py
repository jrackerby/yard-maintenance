"""Self-test for the yard task table.

WHY THIS EXISTS. every assertion set needs a self-test proving it CAN
fail. The table is one rule read by six sensors, so a mistake in it is not one
wrong entity -- it is every yard entity wrong the same way. It is also the
exact shape that rots unnoticed: a maintenance rollup reads "0 due" on a
healthy yard and on a completely broken one alike.

So every leg gets a case that MUST produce its verdict, paired with a near-miss
that must NOT. The suppression leg and the unset-versus-overdue leg matter
most, because both of their failure modes are silent green.

WHAT THIS DOES NOT PROVE. It runs with no Home Assistant, so it says nothing
about whether the entities exist, whether the store round-trips, or what the
Navimow reports. Those need the installation. What it does prove is the
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
active_holds = _tasks.active_holds
hold_rows = _tasks.hold_rows
hold_until = _tasks.hold_until
next_hold_boundary = _tasks.next_hold_boundary
HOLDS = sys.modules[f"{_PKG}.const"].HOLDS

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
    "fine" is the empty-inventory failure recorded elsewhere.
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

    a monitor whose blind spot correlates with what it monitors. If
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
    """A declined signal is STATED, never silently dropped."""
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
    """`Mixed` resolves to unknown deliberately -- never guess."""
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


# -- holds ----------------------------------------------------------------
#
# Two consumers, two questions. Every case checks BOTH answers, because the
# failure that matters is the one where they agree when they should not: a
# fertiliser that holds the sprinklers (starving a feed of the water it
# needs) or a weed treatment that lets the mower out (cutting off the leaf it
# landed on).

HOUR = 3600.0


def hours_ago(n: float) -> float:
    """Epoch seconds `n` hours before the frozen clock."""
    return NOW_TS - n * HOUR


def hold(rows, key):
    """One hold row by key, or a readable failure."""
    for r in rows:
        if r["key"] == key:
            return r
    raise AssertionError(f"no hold {key!r}; keys={[r['key'] for r in rows]}")


def test_fresh_yard_holds_nothing():
    """No application, no stand-off -- and that is `off`, not unknown."""
    rows = hold_rows(build())
    assert rows == []
    assert active_holds(rows, "mow") == []
    assert active_holds(rows, "irrigation") == []
    assert hold_until(rows, "mow") == 0
    assert next_hold_boundary(rows, NOW_TS) is None


def test_weed_control_holds_both_then_only_the_mower():
    """Liquid post-emergent: dry leaf for a day, uncut leaf for two."""
    fresh = hold_rows(build(dates={"weed_control": hours_ago(1)}))
    assert hold(fresh, "weed_control")["mow"] is True
    assert hold(fresh, "weed_control")["irrigation"] is True
    assert hold(fresh, "weed_control")["kind"] == "chemical"

    dried = hold_rows(build(dates={"weed_control": hours_ago(30)}))
    assert hold(dried, "weed_control")["mow"] is True
    assert hold(dried, "weed_control")["irrigation"] is False

    lapsed = hold_rows(build(dates={"weed_control": hours_ago(50)}))
    assert hold(lapsed, "weed_control")["mow"] is False
    assert hold(lapsed, "weed_control")["irrigation"] is False
    # The row is still emitted once lapsed -- a surface may want the history --
    # but it is not ACTIVE for either consumer.
    assert active_holds(lapsed, "mow") == []


def test_granular_products_never_hold_irrigation():
    """A feed, a pre-emergent and a grub treatment all WANT watering in.

    THIS IS THE CASE THAT MUST NOT REGRESS. Holding the sprinklers after a
    granular application is worse than no hold at all.
    """
    for key in ("fertilizer", "pre_emergent", "grub_control"):
        rows = hold_rows(build(dates={key: hours_ago(1)}))
        assert hold(rows, key)["mow"] is True, key
        assert hold(rows, key)["irrigation"] is False, key
        assert hold(rows, key)["irrigation_until"] == 0, key


def test_overseed_is_a_three_week_maintenance_hold_on_the_mower_only():
    """Seedlings are not cut and are never left dry."""
    rows = hold_rows(build(dates={"overseed": days_ago(10)}))
    assert hold(rows, "overseed")["kind"] == "maintenance"
    assert hold(rows, "overseed")["mow"] is True
    assert hold(rows, "overseed")["irrigation"] is False
    assert hold_until(rows, "mow") == int(days_ago(10) + 21 * DAY)

    grown = hold_rows(build(dates={"overseed": days_ago(22)}))
    assert hold(grown, "overseed")["mow"] is False


def test_tasks_outside_the_table_hold_nothing():
    """Lime, a soil test and a cut are not stand-offs."""
    rows = hold_rows(
        build(dates={k: hours_ago(1) for k in ("lime", "soil_test", "mow", "edging")})
    )
    assert rows == []


def test_sentinel_and_missing_dates_hold_nothing():
    """The never-recorded floor is not an application at the epoch."""
    for stamp in (0.0, EPOCH_2000, None):
        rows = hold_rows(build(dates={"fungicide": stamp}))
        assert rows == []


def test_a_future_stamp_holds_nothing_until_it_arrives():
    """A task logged for tomorrow is a plan; the hold starts when it does."""
    rows = hold_rows(build(dates={"fungicide": NOW_TS + 2 * HOUR}))
    assert hold(rows, "fungicide")["mow"] is False
    assert hold(rows, "fungicide")["irrigation"] is False
    assert next_hold_boundary(rows, NOW_TS) == NOW_TS + 2 * HOUR


def test_active_holds_name_the_one_lifting_last():
    """Two overlapping holds: the consumer waits on the later one."""
    rows = hold_rows(
        build(dates={"fungicide": hours_ago(20), "weed_control": hours_ago(10)})
    )
    mow = active_holds(rows, "mow")
    assert [r["key"] for r in mow] == ["weed_control", "fungicide"]
    assert hold_until(rows, "mow") == hold(rows, "weed_control")["mow_until"]
    # Irrigation: fungicide's 24h dry window lifts in 4h, weed control's in 14h.
    irrigation = active_holds(rows, "irrigation")
    assert [r["key"] for r in irrigation] == ["weed_control", "fungicide"]


def test_next_boundary_is_the_soonest_future_edge():
    """The coordinator refreshes at the first edge, whichever consumer's."""
    rows = hold_rows(
        build(dates={"fungicide": hours_ago(20), "weed_control": hours_ago(10)})
    )
    # fungicide lifts both in 4h; that is the first thing that changes.
    assert next_hold_boundary(rows, NOW_TS) == pytest.approx(hours_ago(20) + 24 * HOUR)
    # Past every edge, nothing is left to wait for.
    assert next_hold_boundary(rows, NOW_TS + 100 * HOUR) is None


def test_unknown_hold_target_is_refused():
    """A consumer nobody defined is a typo, not an empty answer."""
    with pytest.raises(ValueError):
        active_holds([], "sprinkler")


def test_every_hold_key_is_a_dated_task():
    """The hold table can only ever read a date the task table records."""
    dated = {r["key"] for r in task_rows(build())}
    assert set(HOLDS) <= dated, set(HOLDS) - dated


def test_the_suite_can_fail():
    """an assertion set that cannot fail is not a gate.

    Proves the table actually responds to its inputs rather than returning a
    constant that happens to satisfy everything above.
    """
    fresh = task_rows(build())
    stale = task_rows(build(dates={k: days_ago(4000) for k in ALL_KEYS}))
    assert len(overdue_rows(fresh)) == 0
    assert len(overdue_rows(stale)) == 12
    assert fresh != stale
