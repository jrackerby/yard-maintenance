"""Constants for the yard maintenance integration.

Everything here moved verbatim in meaning from `custom_templates/
yard_tasks.jinja`, which this component replaces (GH-605). Where a number
changed, it is called out on the line.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "yard_maintenance"

STORAGE_KEY: Final = f"{DOMAIN}.state"
STORAGE_VERSION: Final = 1

# 2000-01-01. Anything at or below this is the "never recorded" sentinel.
#
# THE SENTINEL SURVIVES THE PORT EVEN THOUGH ITS ORIGINAL REASON DOES NOT.
# It existed because a fresh `input_datetime` with no restored state comes up
# seeded to TODAY, silently asserting the work was just done -- the whole
# reason `script.yard_seed_helpers` had to exist. This component stores its
# own state and simply has no unset value to misread, so nothing here can come
# up claiming today.
#
# It is kept anyway, as the WIRE value for "never recorded", because the three
# states are still three (LAW §11: `ok at zero` and `could not read` do not
# collapse) and because a stored 0 and a stored None must resolve identically
# for a slot nobody has touched. Compare against it, never construct it.
EPOCH_2000: Final = 946684800

# Cool-season turf grows in spring and autumn and is renovated in autumn;
# warm-season turf grows in summer heat and is renovated in early summer.
# Every date window and the one suppression rule turn on this split and on
# nothing else.
COOL_SEASON: Final[tuple[str, ...]] = (
    "Tall Fescue",
    "Kentucky Bluegrass",
    "Perennial Ryegrass",
    "Fescue Blend",
)
WARM_SEASON: Final[tuple[str, ...]] = (
    "Bermuda",
    "Zoysia",
    "Centipede",
    "St. Augustine",
    "Bahia",
)

GRASS_UNSET: Final = "Not set"

# FIRST OPTION IS THE UNSET ONE, AND THAT IS LOAD-BEARING for the select
# entity the same way it was for the input_select: a picker with no stored
# value must not assert a grass type nobody chose. `Mixed` resolves to unknown
# rather than to the larger half -- a mixed stand has no single renovation
# window and guessing one would be a schedule built on nothing (LAW §14).
GRASS_OPTIONS: Final[tuple[str, ...]] = (
    (GRASS_UNSET,) + COOL_SEASON + WARM_SEASON + ("Mixed",)
)

PLANT_KIND_UNSET: Final = "Not set"
PLANT_KIND_OPTIONS: Final[tuple[str, ...]] = (
    PLANT_KIND_UNSET,
    "Tree",
    "Shrub",
    "Hedge",
    "Ornamental",
)

# PLANT SLOTS ARE NO LONGER A CEILING IMPOSED BY THE PLATFORM.
#
# The package capped these at 8 because "a package cannot create a helper at
# runtime", and `yard_tasks.jinja` had to declare the same number a second
# time with a join test keeping the two honest. A component creates its own
# entities, so the cap is now a plain default and lives in exactly one place.
# It is still a fixed count per config entry rather than unbounded: entities
# are created at setup, and a slot that can appear mid-run is a registry
# churn problem nobody asked for.
DEFAULT_PLANT_SLOTS: Final = 8
CONF_PLANT_SLOTS: Final = "plant_slots"

# The mower whose sessions feed the mow ledger and the blade clock.
CONF_MOWER_ENTITY: Final = "mower_entity"

# Cadences in days for the lawn programme. These are recommended intervals,
# not observations, so they are constants: the four a household genuinely
# re-tunes (mow, line trim, edging, fertiliser) are editable entities instead.
LAWN_CADENCE: Final[dict[str, int]] = {
    "pre_emergent": 180,
    "weed_control": 90,
    "grub_control": 365,
    "fungicide": 365,
    "lime": 1095,
    "soil_test": 1095,
    "aeration": 365,
    "overseed": 365,
}

LAWN_LABEL: Final[dict[str, str]] = {
    "pre_emergent": "Pre-emergent",
    "weed_control": "Broadleaf weed control",
    "grub_control": "Grub and insect control",
    "fungicide": "Fungicide",
    "lime": "Lime",
    "soil_test": "Soil test",
    "aeration": "Core aeration",
    "overseed": "Overseed",
}

FERTILIZER_KEY: Final = "fertilizer"
FERTILIZER_LABEL: Final = "Fertilise lawn"

# key -> (label, default interval in days). All three read an editable
# interval: how often a yard is cut, trimmed and edged is a household's own
# rhythm and a season's weather, not an agronomic constant.
TURF_ROWS: Final[dict[str, tuple[str, int]]] = {
    "mow": ("Mow", 7),
    "line_trim": ("Line trim", 14),
    "edging": ("Edge", 21),
}

# Every dated lawn/turf task, in the order the table emits them.
LAWN_KEYS: Final[tuple[str, ...]] = (FERTILIZER_KEY,) + tuple(LAWN_CADENCE)
DATED_KEYS: Final[tuple[str, ...]] = LAWN_KEYS + tuple(TURF_ROWS)

GROUP_LAWN: Final = "lawn"
GROUP_TURF: Final = "turf"
GROUP_PLANT: Final = "plant"

# Renovation windows as (month, day) inclusive bounds.
COOL_WINDOW: Final = ((9, 1), (10, 15))
WARM_WINDOW: Final = ((5, 15), (7, 31))

SUPPRESS_OVERSEED_WARM: Final = (
    "warm-season turf is renovated by aeration, not seed"
)

# Defaults for the editable numbers, matching what script.yard_seed_helpers
# wrote. The package additionally relied on an input_number with no restored
# state coming up at its `min`, which is why every min was chosen to be a
# defensible cadence rather than the smallest legal one. Stored state removes
# that hazard, but the defaults are unchanged so a fresh install behaves the
# way the seeded package did.
DEFAULT_FERTILIZER_INTERVAL: Final = 60.0
DEFAULT_BLADE_LIFE_HOURS: Final = 80.0
DEFAULT_PLANT_PRUNE_INTERVAL: Final = 365.0
DEFAULT_PLANT_TREAT_INTERVAL: Final = 365.0

# Blade wear is accumulated from observed mow sessions because the cloud will
# not give it (GH-548): Segway keeps blade wear in the phone app and exposes
# none of it. The figure is only ever as good as the sessions this ledger
# closes, which is why it is documented as a floor, never a total.
BLADE_BASIS: Final = (
    "accumulated from observed mow sessions; a floor, not a total"
)

ATTRIBUTION: Final = "Derived in the estate from recorded yard work"
MANUFACTURER: Final = "Estate"

# Mower states. The ledger interlocks on its own session stamp rather than on
# the mower's state, deliberately: a lost uplink resolves to `unknown`, and
# `idle`/`charging` both resolve to `docked`, so a dock transition alone is
# not evidence a mow happened.
MOWER_MOWING: Final = "mowing"
MOWER_DOCKED: Final = "docked"
MOWER_PAUSED: Final = "paused"

SERVICE_LOG_TASK: Final = "log_task"
SERVICE_LOG_PLANT_TASK: Final = "log_plant_task"
SERVICE_BLADE_CHANGED: Final = "blade_changed"
