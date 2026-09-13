"""A yard maintenance tracker.

Replaces `an earlier YAML package` (87 helpers, 5 template sensors, 3
binary sensors, 4 automations, 3 scripts) and a Jinja macro.

WHAT MOVED AND WHAT DID NOT. The task rule moved verbatim into `tasks.py` and
was proved identical against the macro before the macro was deleted. The two
mow-session automations moved here, into `_async_mower_changed`, because they
are a state machine over one entity and a state machine spread across two YAML
automations with a shared interlock helper is the thing that made them hard to
reason about. The three scripts became services.

`script.yard_seed_helpers` moved NOWHERE. It existed only because a fresh
`input_datetime` comes up seeded to TODAY, so an untouched yard claimed every
job had just been done, and the one-shot wrote a 1970 sentinel over all of
them -- gated, self-disarming, and destructive if it ever ran twice. Stored
state has no such value to misread. The workaround is deleted, not ported.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    CONF_MOWER_ENTITY,
    CONF_PLANT_SLOTS,
    DATED_KEYS,
    DEFAULT_PLANT_SLOTS,
    DOMAIN,
    EPOCH_2000,
    MOWER_DOCKED,
    MOWER_MOWING,
    MOWER_PAUSED,
    SERVICE_BLADE_CHANGED,
    SERVICE_LOG_PLANT_TASK,
    SERVICE_LOG_TASK,
)
from .coordinator import YardCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.DATETIME,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.TEXT,
]

type YardConfigEntry = ConfigEntry[YardCoordinator]

# A cut cannot be shorter than this and still be a cut. The mower leaves the
# dock for a self-check and returns, and NavimowHA reports that as mowing ->
# docked exactly like a real session; stamping it would reset the mow clock
# and add minutes to the blade for work that never happened.
MIN_SESSION_MINUTES = 2.0

# Nor longer than this. A missed dock event leaves the session open until the
# next one, which would otherwise book a day and a half of blade wear in one
# go. Capped rather than discarded: the cut did happen, the duration is what
# is unknown.
MAX_SESSION_MINUTES = 600.0

LOG_TASK_SCHEMA = vol.Schema(
    {
        vol.Required("key"): vol.In(DATED_KEYS),
        vol.Optional("when"): cv.datetime,
    }
)

LOG_PLANT_TASK_SCHEMA = vol.Schema(
    {
        vol.Required("slot"): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Required("action"): vol.In(("prune", "treat")),
        vol.Optional("when"): cv.datetime,
    }
)

BLADE_CHANGED_SCHEMA = vol.Schema(
    {
        vol.Optional("at_hours"): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Optional("when"): cv.datetime,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: YardConfigEntry) -> bool:
    """Set up the yard from a config entry."""
    coordinator = YardCoordinator(
        hass,
        entry.entry_id,
        int(entry.options.get(CONF_PLANT_SLOTS, DEFAULT_PLANT_SLOTS)),
    )
    coordinator.config_entry = entry
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_services(hass, coordinator)

    mower = entry.options.get(CONF_MOWER_ENTITY) or entry.data.get(CONF_MOWER_ENTITY)
    if mower:
        entry.async_on_unload(
            async_track_state_change_event(
                hass, [mower], _make_mower_listener(coordinator)
            )
        )

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    # The coordinator books a point-in-time refresh at every hold boundary;
    # an unload that leaves that timer standing refreshes a coordinator whose
    # entities are gone.
    entry.async_on_unload(coordinator.async_shutdown)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: YardConfigEntry) -> bool:
    """Unload exactly the platforms that were forwarded."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: YardConfigEntry) -> None:
    """Options changed -- plant slot count or mower can both move."""
    await hass.config_entries.async_reload(entry.entry_id)


def _make_mower_listener(coordinator: YardCoordinator):
    """Build the mow-session state machine bound to one coordinator."""

    async def _async_mower_changed(event: Event) -> None:
        """Open a session on `mowing`, close and stamp it on `docked`.

        THE INTERLOCK IS THE STORED SESSION STAMP, NOT THE MOWER'S STATE, and
        that is deliberate. A lost uplink resolves to `unknown`, and both
        `idle` and `charging` resolve to `docked`, so a dock transition on its
        own is not evidence that grass was cut -- matching any -> docked would
        stamp a mow every time the mower finished a self-check. `error` is
        likewise never a completed cut.

        A resume from pause continues the session it is already in; only an
        entry from a non-cutting state starts a new one, or a mower that
        paused twice would report the last leg as the whole mow.
        """
        old = event.data.get("old_state")
        new = event.data.get("new_state")
        if new is None or old is None:
            return

        started = coordinator.state["mow"].get("started")
        session_open = bool(started and started > EPOCH_2000)
        now = dt_util.utcnow().timestamp()

        if new.state == MOWER_MOWING:
            if old.state in (MOWER_MOWING, MOWER_PAUSED) or session_open:
                return
            await coordinator.async_update_mow(started=now)
            _LOGGER.info("Mow session opened on %s", event.data.get("entity_id"))
            return

        if new.state == MOWER_DOCKED and session_open:
            minutes = (now - float(started)) / 60
            if minutes < MIN_SESSION_MINUTES:
                # Not a cut. Close the session without booking anything --
                # and say so, because a silently dropped session is
                # indistinguishable from a listener that never fired.
                _LOGGER.info(
                    "Mow session closed after %.1f min, below the %.0f min floor:"
                    " recorded as a dock excursion, not a cut",
                    minutes,
                    MIN_SESSION_MINUTES,
                )
                await coordinator.async_update_mow(started=None)
                return

            capped = min(minutes, MAX_SESSION_MINUTES)
            if capped < minutes:
                _LOGGER.warning(
                    "Mow session ran %.0f min, over the %.0f min ceiling -- a dock"
                    " event was probably missed. Booking the ceiling; the cut is"
                    " real, its duration is not known",
                    minutes,
                    MAX_SESSION_MINUTES,
                )

            blade_minutes = float(coordinator.state["blade"].get("minutes") or 0.0)
            coordinator.state["dates"]["mow"] = now
            coordinator.state["blade"]["minutes"] = blade_minutes + capped
            await coordinator.async_update_mow(
                started=None,
                last_minutes=round(capped, 1),
                sessions=int(coordinator.state["mow"].get("sessions") or 0) + 1,
                source=event.data.get("entity_id", ""),
            )
            _LOGGER.info("Mow session closed and stamped: %.1f min", capped)

    return _async_mower_changed


def _register_services(hass: HomeAssistant, coordinator: YardCoordinator) -> None:
    """Register the three services that replace the package's scripts."""

    async def _async_log_task(call: ServiceCall) -> None:
        """Record that a dated lawn or turf task just happened."""
        when: Any = call.data.get("when")
        stamp = when.timestamp() if when else dt_util.utcnow().timestamp()
        await coordinator.async_set_date(call.data["key"], stamp)

    async def _async_log_plant_task(call: ServiceCall) -> None:
        """Record a prune or a treatment against one plant slot."""
        when: Any = call.data.get("when")
        stamp = when.timestamp() if when else dt_util.utcnow().timestamp()
        field = (
            "last_pruned" if call.data["action"] == "prune" else "last_treated"
        )
        slot = int(call.data["slot"])
        if str(slot) not in coordinator.state["plants"]:
            _LOGGER.warning(
                "Plant slot %s does not exist; this yard has %s slots",
                slot,
                coordinator.plant_slots,
            )
            return
        await coordinator.async_update_plant(slot, **{field: stamp})

    async def _async_blade_changed(call: ServiceCall) -> None:
        """Record a blade change and restart the wear clock.

        `at_hours` exists for the case the package's own comment called out:
        a blade whose age is estimated rather than observed. It seeds the
        counter instead of pretending the blade is new.
        """
        when: Any = call.data.get("when")
        stamp = when.timestamp() if when else dt_util.utcnow().timestamp()
        at_hours = float(call.data.get("at_hours", 0.0))
        await coordinator.async_update_blade(
            changed=stamp, minutes=at_hours * 60
        )

    hass.services.async_register(
        DOMAIN, SERVICE_LOG_TASK, _async_log_task, schema=LOG_TASK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LOG_PLANT_TASK,
        _async_log_plant_task,
        schema=LOG_PLANT_TASK_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_BLADE_CHANGED,
        _async_blade_changed,
        schema=BLADE_CHANGED_SCHEMA,
    )
