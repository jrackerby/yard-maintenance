"""Shared entity base.

THE DEVICE IS NAMED "Yard" AND THAT IS A CONTRACT, NOT A LABEL.

With `_attr_has_entity_name = True`, Home Assistant builds an entity_id by
slugifying "<device name> <entity name>". The board reads the old template
sensors by id -- `sensor.yard_maintenance_due_count`, `sensor.yard_next_task`,
`sensor.yard_days_since_mow`, `sensor.yard_blade_hours`,
`sensor.yard_grass_program`, and the three `binary_sensor.yard_*` -- so a
device named "Yard" plus entity names carrying the rest reproduces every one
of them exactly, and no dashboard has to change for the read-only half of the
migration.

Rename this device and every one of those ids changes silently, on a surface
that will keep rendering the last value it saw. TOOLS.md: ids are assigned at
creation and never update, and a platform that finds its id occupied takes
`_2` and never gives it back -- which is also why migration must delete the
old template rows from the registry before this component starts, rather than
after.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN, MANUFACTURER
from .coordinator import YardCoordinator

DEVICE_NAME = "Yard"


class YardEntity(CoordinatorEntity[YardCoordinator]):
    """One yard, as a service device."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: YardCoordinator, key: str) -> None:
        """Bind to the yard device and claim a stable unique id."""
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
            entry_type=DeviceEntryType.SERVICE,
            manufacturer=MANUFACTURER,
            model="Yard maintenance",
            name=DEVICE_NAME,
        )

    @property
    def available(self) -> bool:
        """Always available.

        LAW §11: the coordinator never raises, and every entity overrides
        `available` to true. A tracker that disappears when it cannot compute
        is the failure the contract exists to refuse -- and unlike a device
        integration there is nothing here to be unreachable FROM. The one
        entity that genuinely has a third state, `binary_sensor.yard_blade_due`,
        expresses it through `availability` on its own, because a blade whose
        change was never recorded is not a blade that is fine.
        """
        return True
