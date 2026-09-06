"""Every date the yard records, as editable entities.

THESE REPLACE 30 `input_datetime` HELPERS AND FIX THEIR ONE REAL DEFECT. A
fresh `input_datetime` with no restored state comes up seeded to TODAY, so an
untouched yard asserted that all twelve jobs had just been done -- the entire
reason `script.yard_seed_helpers` had to exist, gated and self-disarming
because running it twice destroyed real history.

A `DateTimeEntity` backed by stored state returns None when nothing has been
recorded, which HA renders as `unknown`. Never-recorded and recorded-today are
different values again, with no one-shot to run and nothing to run twice.

The entity ids move domain -- `input_datetime.yard_last_mow` becomes
`datetime.yard_last_mow` -- because an integration cannot create an
`input_datetime`. That is unavoidable and the board carries the two it reads.
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import YardConfigEntry
from .const import DATED_KEYS, EPOCH_2000
from .coordinator import YardCoordinator
from .entity import YardEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YardConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up every date the yard tracks."""
    coordinator = entry.runtime_data
    entities: list[DateTimeEntity] = [
        YardTaskDate(coordinator, key) for key in DATED_KEYS
    ]
    entities.append(YardBladeChangedDate(coordinator))
    entities.append(YardMowStartedDate(coordinator))
    for index in range(1, coordinator.plant_slots + 1):
        entities.append(YardPlantDate(coordinator, index, "last_pruned", "Last Pruned"))
        entities.append(
            YardPlantDate(coordinator, index, "last_treated", "Last Treated")
        )
    async_add_entities(entities)


def _to_dt(value: float | None) -> datetime | None:
    """Stored epoch -> aware datetime, with the sentinel reading as unset."""
    if not value or value <= EPOCH_2000:
        return None
    return dt_util.utc_from_timestamp(float(value))


class YardTaskDate(YardEntity, DateTimeEntity):
    """When a dated lawn or turf task last happened."""

    _attr_icon = "mdi:calendar-check"

    def __init__(self, coordinator: YardCoordinator, key: str) -> None:
        """Bind to one task key."""
        super().__init__(coordinator, f"last_{key}")
        self._task_key = key
        # NAMED FROM THE KEY, NOT THE LABEL, AND THAT IS DELIBERATE. The
        # label for `fertilizer` is "Fertilise lawn", which would slugify to
        # `datetime.yard_last_fertilise_lawn` -- an id nobody would guess and
        # one that does not match the helper it replaces. The old
        # `input_datetime` ids were built from the key, so these are too:
        # "Last Pre Emergent" -> datetime.yard_last_pre_emergent.
        self._attr_name = "Last " + key.replace("_", " ").title()

    @property
    def native_value(self) -> datetime | None:
        """The recorded date, or None when never recorded."""
        return _to_dt(self.coordinator.state["dates"].get(self._task_key))

    async def async_set_value(self, value: datetime) -> None:
        """Record a date by hand."""
        await self.coordinator.async_set_date(self._task_key, value.timestamp())


class YardBladeChangedDate(YardEntity, DateTimeEntity):
    """When the blade was last changed."""

    _attr_name = "Blade Changed"
    _attr_icon = "mdi:saw-blade"

    def __init__(self, coordinator: YardCoordinator) -> None:
        """Bind."""
        super().__init__(coordinator, "blade_changed")

    @property
    def native_value(self) -> datetime | None:
        """The change date, or None when unrecorded."""
        return _to_dt(self.coordinator.state["blade"].get("changed"))

    async def async_set_value(self, value: datetime) -> None:
        """Set the change date without touching the accumulated minutes.

        Use the `yard_maintenance.blade_changed` service to record a NEW
        blade -- that resets the wear clock. This setter only corrects the
        date, which is what a hand edit almost always means.
        """
        await self.coordinator.async_update_blade(changed=value.timestamp())


class YardMowStartedDate(YardEntity, DateTimeEntity):
    """The open mow session's start, and the ledger's interlock.

    Editable because a stuck session -- a missed dock event, a restart
    mid-cut -- otherwise has no way back. Clearing it closes the session
    without booking anything.
    """

    _attr_name = "Mow Started"
    _attr_icon = "mdi:play-circle-outline"

    def __init__(self, coordinator: YardCoordinator) -> None:
        """Bind."""
        super().__init__(coordinator, "mow_started")

    @property
    def native_value(self) -> datetime | None:
        """The open session's start, or None when no session is open."""
        return _to_dt(self.coordinator.state["mow"].get("started"))

    async def async_set_value(self, value: datetime) -> None:
        """Open or correct a session by hand."""
        await self.coordinator.async_update_mow(started=value.timestamp())


class YardPlantDate(YardEntity, DateTimeEntity):
    """One plant's prune or treat date."""

    _attr_icon = "mdi:calendar-check"

    def __init__(
        self, coordinator: YardCoordinator, index: int, field: str, label: str
    ) -> None:
        """Bind to one slot and one of its two clocks."""
        super().__init__(coordinator, f"plant_{index}_{field}")
        self._index = index
        self._field = field
        self._attr_name = f"Plant {index} {label}"

    @property
    def native_value(self) -> datetime | None:
        """The recorded date, or None when never recorded."""
        return _to_dt(self.coordinator.plant_slot(self._index).get(self._field))

    async def async_set_value(self, value: datetime) -> None:
        """Record a date by hand."""
        await self.coordinator.async_update_plant(
            self._index, **{self._field: value.timestamp()}
        )
