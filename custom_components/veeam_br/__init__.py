"""The Veeam Backup & Replication integration."""

from __future__ import annotations

from datetime import timedelta
import importlib
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    API_VERSIONS,
    CONF_API_VERSION,
    CONF_VERIFY_SSL,
    DEFAULT_API_VERSION,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    UPDATE_INTERVAL,
)
from .token_manager import VeeamTokenManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Veeam Backup & Replication from a config entry."""
    api_version = entry.options.get(
        CONF_API_VERSION, entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION)
    )
    api_module = API_VERSIONS.get(api_version, "v1_3_rev1")

    # Import API endpoint dynamically
    try:
        get_all_jobs = importlib.import_module(f"veeam_br.{api_module}.api.jobs.get_all_jobs")
    except ImportError as err:
        _LOGGER.error("Failed to import veeam_br jobs API: %s", err)
        return False

    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    base_url = f"https://{host}:{port}"

    token_manager = VeeamTokenManager(
        base_url=base_url,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        api_version=api_version,
    )

    async def async_update_data():
        """Fetch data from API."""
        try:
            if not await token_manager.ensure_valid_token(hass):
                raise UpdateFailed("Failed to obtain valid access token")

            client = token_manager.get_authenticated_client()
            if not client:
                raise UpdateFailed("No authenticated client available")

            def _get_jobs():
                return get_all_jobs.sync_detailed(
                    client=client,
                    x_api_version=api_version,
                )

            response = await hass.async_add_executor_job(_get_jobs)

            if response.status_code != 200:
                raise UpdateFailed(f"API returned status {response.status_code}")

            jobs_data = response.parsed or []
            if not isinstance(jobs_data, list):
                return []

            return [
                {
                    "id": job.id,
                    "name": job.name or "Unknown",
                    "status": job.status or "unknown",
                    "type": job.type,
                    "last_run": job.last_run,
                    "next_run": job.next_run,
                    "last_result": job.last_result,
                }
                for job in jobs_data
            ]

        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=UPDATE_INTERVAL),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "token_manager": token_manager,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(update_listener))

    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
