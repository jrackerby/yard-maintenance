"""Config and options flow.

ONE YARD PER INSTALL. `async_abort` on a second entry rather than allowing
several: every entity id here is unprefixed (`sensor.yard_next_task`), so a
second entry would take `_2` on all of them and the board would keep reading
the first one forever while the user edited the second (TOOLS.md).
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_MOWER_ENTITY,
    CONF_PLANT_SLOTS,
    DEFAULT_PLANT_SLOTS,
    DOMAIN,
)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    """The one form, used by both flows.

    The mower is OPTIONAL and selected from the `lawn_mower` domain rather
    than typed. Optional because the tracker is useful with no mower at all --
    every task can be logged by hand -- and because pinning an entity id that
    does not exist yet is how a config flow certifies something green that was
    never wired (LAW §9).
    """
    return vol.Schema(
        {
            vol.Optional(
                CONF_MOWER_ENTITY,
                description={"suggested_value": defaults.get(CONF_MOWER_ENTITY)},
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="lawn_mower")
            ),
            vol.Required(
                CONF_PLANT_SLOTS,
                default=defaults.get(CONF_PLANT_SLOTS, DEFAULT_PLANT_SLOTS),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=32, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )


class YardMaintenanceConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set the yard up."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the mower and the plant slot count."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(
                title="Yard",
                data={},
                options={
                    CONF_MOWER_ENTITY: user_input.get(CONF_MOWER_ENTITY),
                    CONF_PLANT_SLOTS: int(user_input[CONF_PLANT_SLOTS]),
                },
            )

        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> YardMaintenanceOptionsFlow:
        """Options are the same two questions."""
        return YardMaintenanceOptionsFlow()


class YardMaintenanceOptionsFlow(OptionsFlow):
    """Change the mower or the number of plant slots."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the same form, seeded from current options."""
        if user_input is not None:
            # MERGE OVER `entry.options`, NEVER RETURN ONLY THIS STEP'S KEYS.
            # `async_create_entry(data=...)` REPLACES options wholesale, so a
            # step returning its own keys deletes every other step's, silently
            # and with no edit to point at (TOOLS.md). Harmless while there is
            # one step, which is exactly how it survives to the second.
            return self.async_create_entry(
                data={
                    **self.config_entry.options,
                    CONF_MOWER_ENTITY: user_input.get(CONF_MOWER_ENTITY),
                    CONF_PLANT_SLOTS: int(user_input[CONF_PLANT_SLOTS]),
                }
            )

        return self.async_show_form(
            step_id="init", data_schema=_schema(dict(self.config_entry.options))
        )
