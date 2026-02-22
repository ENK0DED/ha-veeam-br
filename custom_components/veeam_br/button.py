"""Support for Veeam Backup & Replication buttons."""

from __future__ import annotations

import asyncio
import importlib
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    API_VERSIONS,
    CONF_API_VERSION,
    DEFAULT_API_VERSION,
    DOMAIN,
    check_api_feature_availability,
)

_LOGGER = logging.getLogger(__name__)

# Limit parallel updates to avoid overwhelming the Veeam API
PARALLEL_UPDATES = 1

_RUNNING_STATUSES = frozenset({"running", "working", "postprocessing", "waitingtape"})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Veeam Backup & Replication buttons."""
    coordinator = entry.runtime_data["coordinator"]
    veeam_client = entry.runtime_data["veeam_client"]

    # Pre-import API endpoint modules to avoid blocking calls in event loop
    # Get the configured API version for proper module path
    api_version = entry.options.get(
        CONF_API_VERSION,
        entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
    )
    api_module = API_VERSIONS.get(api_version, "v1_3_rev1")

    # Pre-import button API endpoints
    button_endpoints = [
        "jobs.start_job",
        "jobs.stop_job",
        "jobs.retry_job",
        "jobs.enable_job",
        "jobs.disable_job",
        "repositories.rescan_repositories",
        "repositories.enable_scale_out_extent_sealed_mode",
        "repositories.disable_scale_out_extent_sealed_mode",
        "repositories.enable_scale_out_extent_maintenance_mode",
        "repositories.disable_scale_out_extent_maintenance_mode",
    ]
    for endpoint in button_endpoints:
        try:
            await asyncio.to_thread(
                importlib.import_module, f"veeam_br.{api_module}.api.{endpoint}"
            )
        except ImportError as err:
            _LOGGER.debug("Could not pre-import %s: %s", endpoint, err)

    added_repository_ids: set[str] = set()
    added_sobr_extent_ids: set[tuple[str, str]] = set()  # (sobr_id, extent_id) tuples
    added_job_ids: set[str] = set()

    @callback
    def _sync_entities() -> None:
        if not coordinator.data:
            return

        # Get the configured API version
        api_version = entry.options.get(
            CONF_API_VERSION,
            entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
        )

        new_entities = []

        # Create buttons for each job
        for job in coordinator.data.get("jobs", []):
            job_id = job.get("id")
            if not job_id or job_id in added_job_ids:
                continue

            job_buttons = []

            # Check if each button type's API feature is available before creating
            if check_api_feature_availability(api_version, "models.job_start_spec"):
                job_buttons.append(VeeamJobStartButton(coordinator, entry, job, veeam_client))

            if check_api_feature_availability(api_version, "models.job_stop_spec"):
                job_buttons.append(VeeamJobStopButton(coordinator, entry, job, veeam_client))

            if check_api_feature_availability(api_version, "models.job_retry_spec"):
                job_buttons.append(VeeamJobRetryButton(coordinator, entry, job, veeam_client))

            if check_api_feature_availability(api_version, "api.jobs"):
                job_buttons.append(VeeamJobEnableButton(coordinator, entry, job, veeam_client))
                job_buttons.append(VeeamJobDisableButton(coordinator, entry, job, veeam_client))

            new_entities.extend(job_buttons)
            added_job_ids.add(job_id)
            _LOGGER.debug(
                "Adding %d buttons for job: %s (id: %s)",
                len(job_buttons),
                job.get("name"),
                job_id,
            )

        # Create rescan button for each repository
        if check_api_feature_availability(api_version, "models.repositories_rescan_spec"):
            for repository in coordinator.data.get("repositories", []):
                repo_id = repository.get("id")
                if not repo_id or repo_id in added_repository_ids:
                    continue

                new_entities.append(
                    VeeamRepositoryRescanButton(coordinator, entry, repository, veeam_client)
                )
                added_repository_ids.add(repo_id)
                _LOGGER.debug(
                    "Adding rescan button for repository: %s (id: %s)",
                    repository.get("name"),
                    repo_id,
                )

        # Create buttons for each SOBR extent
        if check_api_feature_availability(api_version, "models.scale_out_extent_maintenance_spec"):
            for sobr in coordinator.data.get("sobrs", []):
                sobr_id = sobr.get("id")
                sobr_name = sobr.get("name", "Unknown SOBR")
                if not sobr_id:
                    continue

                for extent in sobr.get("extents", []):
                    extent_id = extent.get("id")
                    if not extent_id:
                        continue

                    extent_key = (sobr_id, extent_id)
                    if extent_key in added_sobr_extent_ids:
                        continue

                    # Create 4 buttons for each extent (enable/disable sealed and maintenance mode)
                    new_entities.extend(
                        [
                            VeeamSOBRExtentEnableSealedModeButton(
                                coordinator, entry, sobr, extent, veeam_client
                            ),
                            VeeamSOBRExtentDisableSealedModeButton(
                                coordinator, entry, sobr, extent, veeam_client
                            ),
                            VeeamSOBRExtentEnableMaintenanceModeButton(
                                coordinator, entry, sobr, extent, veeam_client
                            ),
                            VeeamSOBRExtentDisableMaintenanceModeButton(
                                coordinator, entry, sobr, extent, veeam_client
                            ),
                        ]
                    )
                    added_sobr_extent_ids.add(extent_key)
                    _LOGGER.debug(
                        "Adding buttons for SOBR extent: %s/%s (sobr_id: %s, extent_id: %s)",
                        sobr_name,
                        extent.get("name"),
                        sobr_id,
                        extent_id,
                    )

        if new_entities:
            _LOGGER.debug("Adding %d Veeam buttons", len(new_entities))
            async_add_entities(new_entities)

        # Remove stale button entities
        _remove_stale_button_entities(
            hass, entry, added_repository_ids, added_sobr_extent_ids, added_job_ids
        )

    def _remove_stale_button_entities(
        hass: HomeAssistant,
        entry: ConfigEntry,
        current_repo_ids: set[str],
        current_sobr_extent_ids: set[tuple[str, str]],
        current_job_ids: set[str],
    ) -> None:
        """Remove button entities for repos/sobrs/jobs that no longer exist.

        Scans the entity registry directly so that button entities persisted
        from previous HA sessions are also cleaned up, not only those added in
        the current session.
        """
        if not coordinator.data:
            return

        entity_reg = er.async_get(hass)
        entry_id = entry.entry_id

        # Get current IDs from coordinator data
        current_repos_in_data = {
            repo.get("id") for repo in coordinator.data.get("repositories", []) if repo.get("id")
        }
        current_jobs_in_data = {
            job.get("id") for job in coordinator.data.get("jobs", []) if job.get("id")
        }

        # Track current SOBR extents in data
        current_sobr_extents_in_data: set[tuple[str, str]] = set()
        for sobr in coordinator.data.get("sobrs", []):
            sobr_id = sobr.get("id")
            if sobr_id:
                for extent in sobr.get("extents", []):
                    extent_id = extent.get("id")
                    if extent_id:
                        current_sobr_extents_in_data.add((sobr_id, extent_id))

        # Build unique_id prefixes for active entities
        active_job_prefixes = {f"{entry_id}_job_{job_id}_" for job_id in current_jobs_in_data}
        active_repo_prefixes = {
            f"{entry_id}_repository_{repo_id}_" for repo_id in current_repos_in_data
        }
        active_sobr_extent_prefixes = {
            f"{entry_id}_sobr_{sobr_id}_extent_{extent_id}_"
            for sobr_id, extent_id in current_sobr_extents_in_data
        }

        # Scan all registered button entities for this config entry and remove stale ones.
        # Using list() to avoid mutating the iterable while iterating.
        for entity in list(er.async_entries_for_config_entry(entity_reg, entry_id)):
            if not entity.unique_id:
                continue
            unique_id = entity.unique_id

            if unique_id.startswith(f"{entry_id}_job_"):
                if not any(unique_id.startswith(p) for p in active_job_prefixes):
                    _LOGGER.info("Removing stale job button: %s", entity.entity_id)
                    entity_reg.async_remove(entity.entity_id)

            elif unique_id.startswith(f"{entry_id}_repository_"):
                if not any(unique_id.startswith(p) for p in active_repo_prefixes):
                    _LOGGER.info("Removing stale repository button: %s", entity.entity_id)
                    entity_reg.async_remove(entity.entity_id)

            elif unique_id.startswith(f"{entry_id}_sobr_") and "_extent_" in unique_id:
                if not any(unique_id.startswith(p) for p in active_sobr_extent_prefixes):
                    _LOGGER.info("Removing stale SOBR extent button: %s", entity.entity_id)
                    entity_reg.async_remove(entity.entity_id)

        # Update tracking sets to reflect only IDs still present in the API
        current_job_ids.intersection_update(current_jobs_in_data)
        current_repo_ids.intersection_update(current_repos_in_data)
        current_sobr_extent_ids.intersection_update(current_sobr_extents_in_data)

    # First attempt (after first refresh already ran)
    _sync_entities()

    # Future updates
    coordinator.async_add_listener(_sync_entities)


class VeeamRepositoryRescanButton(CoordinatorEntity, ButtonEntity):
    """Button to trigger repository rescan."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, config_entry, repository_data, veeam_client):
        """Initialize the rescan button."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._repo_id = repository_data.get("id")
        self._repo_name = repository_data.get("name", "Unknown Repository")
        self._veeam_client = veeam_client
        self._attr_unique_id = f"{config_entry.entry_id}_repository_{self._repo_id}_rescan"
        self._attr_name = "Rescan"

    @property
    def device_info(self):
        """Return device info for this repository."""
        return {
            "identifiers": {(DOMAIN, f"repository_{self._repo_id}")},
            "name": f"{self._repo_name}",
            "manufacturer": "Veeam",
            "model": "Backup Repository",
        }

    @property
    def available(self) -> bool:
        """Return True if the repository still exists in coordinator data."""
        if not self.coordinator.data:
            return False
        return any(
            repo.get("id") == self._repo_id
            for repo in self.coordinator.data.get("repositories", [])
        )

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:magnify-scan"

    async def async_press(self) -> None:
        """Handle the button press to trigger a repository rescan."""
        api_version = self._config_entry.options.get(
            CONF_API_VERSION,
            self._config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
        )
        api_module = API_VERSIONS.get(api_version, "v1_3_rev1")

        try:
            models_module = await asyncio.to_thread(
                importlib.import_module,
                f"veeam_br.{api_module}.models.repositories_rescan_spec",
            )
            RepositoriesRescanSpec = models_module.RepositoriesRescanSpec
            body = RepositoriesRescanSpec(repository_ids=[self._repo_id])
        except (ImportError, AttributeError) as e:
            _LOGGER.error(
                "Failed to import RepositoriesRescanSpec: %s. Cannot rescan repository.", e
            )
            return

        try:
            repositories_api = await asyncio.to_thread(self._veeam_client.api, "repositories")
            await self._veeam_client.call(
                repositories_api.rescan_repositories,
                body=body,
            )
            _LOGGER.info("Successfully triggered rescan for repository: %s", self._repo_name)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to rescan repository %s: %s", self._repo_name, err)
            raise HomeAssistantError(
                f"Failed to rescan repository '{self._repo_name}'"
            ) from err


# ===========================
# SOBR EXTENT BUTTONS
# ===========================


class VeeamSOBRExtentButtonBase(CoordinatorEntity, ButtonEntity):
    """Base class for SOBR extent buttons."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, config_entry, sobr_data, extent_data, veeam_client):
        """Initialize the SOBR extent button."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._sobr_id = sobr_data.get("id")
        self._sobr_name = sobr_data.get("name", "Unknown SOBR")
        self._extent_id = extent_data.get("id")
        self._extent_name = extent_data.get("name", "Unknown Extent")
        self._veeam_client = veeam_client

    @property
    def device_info(self):
        """Return device info for this SOBR."""
        return {
            "identifiers": {(DOMAIN, f"sobr_{self._sobr_id}")},
            "name": f"{self._sobr_name}",
            "manufacturer": "Veeam",
            "model": "Scale-Out Backup Repository",
        }

    def _get_current_extent_status(self) -> list[str] | None:
        """Find current extent status from coordinator data."""
        if not self.coordinator.data:
            return None
        for sobr in self.coordinator.data.get("sobrs", []):
            if sobr.get("id") != self._sobr_id:
                continue
            for extent in sobr.get("extents", []):
                if extent.get("id") == self._extent_id:
                    return extent.get("status", [])
        return None

    @property
    def available(self) -> bool:
        """Return True if the SOBR extent still exists in coordinator data."""
        return self._get_current_extent_status() is not None


class VeeamSOBRExtentEnableSealedModeButton(VeeamSOBRExtentButtonBase):
    """Button to enable sealed mode for a SOBR extent."""

    def __init__(self, coordinator, config_entry, sobr_data, extent_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, sobr_data, extent_data, veeam_client)
        self._attr_unique_id = (
            f"{config_entry.entry_id}_sobr_{self._sobr_id}_extent_{self._extent_id}"
            f"_enable_sealed_mode"
        )
        self._attr_name = f"{self._extent_name} Enable Sealed Mode"

    @property
    def available(self) -> bool:
        """Return True if the extent is not already sealed."""
        status = self._get_current_extent_status()
        if status is None:
            return False
        return not any(s.lower() == "sealed" for s in status)

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:lock-check"

    async def async_press(self) -> None:
        """Handle the button press to enable sealed mode for the extent."""
        api_version = self._config_entry.options.get(
            CONF_API_VERSION,
            self._config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
        )
        api_module = API_VERSIONS.get(api_version, "v1_3_rev1")

        try:
            models_module = await asyncio.to_thread(
                importlib.import_module,
                f"veeam_br.{api_module}.models.scale_out_extent_maintenance_spec",
            )
            ScaleOutExtentMaintenanceSpec = models_module.ScaleOutExtentMaintenanceSpec
            body = ScaleOutExtentMaintenanceSpec(repository_ids=[self._extent_id])
        except (ImportError, AttributeError) as e:
            _LOGGER.error(
                "Failed to import ScaleOutExtentMaintenanceSpec: %s. Cannot enable sealed mode.",
                e,
            )
            return

        try:
            repositories_api = await asyncio.to_thread(self._veeam_client.api, "repositories")
            await self._veeam_client.call(
                repositories_api.enable_scale_out_extent_sealed_mode,
                id=self._sobr_id,
                body=body,
            )
            _LOGGER.info(
                "Successfully enabled sealed mode for extent %s in SOBR %s",
                self._extent_name,
                self._sobr_name,
            )
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error(
                "Failed to enable sealed mode for extent %s in SOBR %s: %s",
                self._extent_name,
                self._sobr_name,
                err,
            )
            raise HomeAssistantError(
                f"Failed to enable sealed mode for extent '{self._extent_name}'"
            ) from err


class VeeamSOBRExtentDisableSealedModeButton(VeeamSOBRExtentButtonBase):
    """Button to disable sealed mode for a SOBR extent."""

    def __init__(self, coordinator, config_entry, sobr_data, extent_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, sobr_data, extent_data, veeam_client)
        self._attr_unique_id = (
            f"{config_entry.entry_id}_sobr_{self._sobr_id}_extent_{self._extent_id}"
            f"_disable_sealed_mode"
        )
        self._attr_name = f"{self._extent_name} Disable Sealed Mode"

    @property
    def available(self) -> bool:
        """Return True if the extent is currently sealed."""
        status = self._get_current_extent_status()
        if status is None:
            return False
        return any(s.lower() == "sealed" for s in status)

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:lock-open"

    async def async_press(self) -> None:
        """Handle the button press to disable sealed mode for the extent."""
        api_version = self._config_entry.options.get(
            CONF_API_VERSION,
            self._config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
        )
        api_module = API_VERSIONS.get(api_version, "v1_3_rev1")

        try:
            models_module = await asyncio.to_thread(
                importlib.import_module,
                f"veeam_br.{api_module}.models.scale_out_extent_maintenance_spec",
            )
            ScaleOutExtentMaintenanceSpec = models_module.ScaleOutExtentMaintenanceSpec
            body = ScaleOutExtentMaintenanceSpec(repository_ids=[self._extent_id])
        except (ImportError, AttributeError) as e:
            _LOGGER.error(
                "Failed to import ScaleOutExtentMaintenanceSpec: %s. Cannot disable sealed mode.",
                e,
            )
            return

        try:
            repositories_api = await asyncio.to_thread(self._veeam_client.api, "repositories")
            await self._veeam_client.call(
                repositories_api.disable_scale_out_extent_sealed_mode,
                id=self._sobr_id,
                body=body,
            )
            _LOGGER.info(
                "Successfully disabled sealed mode for extent %s in SOBR %s",
                self._extent_name,
                self._sobr_name,
            )
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error(
                "Failed to disable sealed mode for extent %s in SOBR %s: %s",
                self._extent_name,
                self._sobr_name,
                err,
            )
            raise HomeAssistantError(
                f"Failed to disable sealed mode for extent '{self._extent_name}'"
            ) from err


class VeeamSOBRExtentEnableMaintenanceModeButton(VeeamSOBRExtentButtonBase):
    """Button to enable maintenance mode for a SOBR extent."""

    def __init__(self, coordinator, config_entry, sobr_data, extent_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, sobr_data, extent_data, veeam_client)
        self._attr_unique_id = (
            f"{config_entry.entry_id}_sobr_{self._sobr_id}_extent_{self._extent_id}"
            f"_enable_maintenance_mode"
        )
        self._attr_name = f"{self._extent_name} Enable Maintenance Mode"

    @property
    def available(self) -> bool:
        """Return True if the extent is not already in maintenance mode."""
        status = self._get_current_extent_status()
        if status is None:
            return False
        return not any(s.lower() == "maintenancemode" for s in status)

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:tools"

    async def async_press(self) -> None:
        """Handle the button press to enable maintenance mode for the extent."""
        api_version = self._config_entry.options.get(
            CONF_API_VERSION,
            self._config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
        )
        api_module = API_VERSIONS.get(api_version, "v1_3_rev1")

        try:
            models_module = await asyncio.to_thread(
                importlib.import_module,
                f"veeam_br.{api_module}.models.scale_out_extent_maintenance_spec",
            )
            ScaleOutExtentMaintenanceSpec = models_module.ScaleOutExtentMaintenanceSpec
            body = ScaleOutExtentMaintenanceSpec(repository_ids=[self._extent_id])
        except (ImportError, AttributeError) as e:
            _LOGGER.error(
                "Failed to import ScaleOutExtentMaintenanceSpec: %s. "
                "Cannot enable maintenance mode.",
                e,
            )
            return

        try:
            repositories_api = await asyncio.to_thread(self._veeam_client.api, "repositories")
            await self._veeam_client.call(
                repositories_api.enable_scale_out_extent_maintenance_mode,
                id=self._sobr_id,
                body=body,
            )
            _LOGGER.info(
                "Successfully enabled maintenance mode for extent %s in SOBR %s",
                self._extent_name,
                self._sobr_name,
            )
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error(
                "Failed to enable maintenance mode for extent %s in SOBR %s: %s",
                self._extent_name,
                self._sobr_name,
                err,
            )
            raise HomeAssistantError(
                f"Failed to enable maintenance mode for extent '{self._extent_name}'"
            ) from err


class VeeamSOBRExtentDisableMaintenanceModeButton(VeeamSOBRExtentButtonBase):
    """Button to disable maintenance mode for a SOBR extent."""

    def __init__(self, coordinator, config_entry, sobr_data, extent_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, sobr_data, extent_data, veeam_client)
        self._attr_unique_id = (
            f"{config_entry.entry_id}_sobr_{self._sobr_id}_extent_{self._extent_id}"
            f"_disable_maintenance_mode"
        )
        self._attr_name = f"{self._extent_name} Disable Maintenance Mode"

    @property
    def available(self) -> bool:
        """Return True if the extent is currently in maintenance mode."""
        status = self._get_current_extent_status()
        if status is None:
            return False
        return any(s.lower() == "maintenancemode" for s in status)

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:close-circle-outline"

    async def async_press(self) -> None:
        """Handle the button press to disable maintenance mode for the extent."""
        api_version = self._config_entry.options.get(
            CONF_API_VERSION,
            self._config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
        )
        api_module = API_VERSIONS.get(api_version, "v1_3_rev1")

        try:
            models_module = await asyncio.to_thread(
                importlib.import_module,
                f"veeam_br.{api_module}.models.scale_out_extent_maintenance_spec",
            )
            ScaleOutExtentMaintenanceSpec = models_module.ScaleOutExtentMaintenanceSpec
            body = ScaleOutExtentMaintenanceSpec(repository_ids=[self._extent_id])
        except (ImportError, AttributeError) as e:
            _LOGGER.error(
                "Failed to import ScaleOutExtentMaintenanceSpec: %s. "
                "Cannot disable maintenance mode.",
                e,
            )
            return

        try:
            repositories_api = await asyncio.to_thread(self._veeam_client.api, "repositories")
            await self._veeam_client.call(
                repositories_api.disable_scale_out_extent_maintenance_mode,
                id=self._sobr_id,
                body=body,
            )
            _LOGGER.info(
                "Successfully disabled maintenance mode for extent %s in SOBR %s",
                self._extent_name,
                self._sobr_name,
            )
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error(
                "Failed to disable maintenance mode for extent %s in SOBR %s: %s",
                self._extent_name,
                self._sobr_name,
                err,
            )
            raise HomeAssistantError(
                f"Failed to disable maintenance mode for extent '{self._extent_name}'"
            ) from err


# ===========================
# JOB BUTTONS
# ===========================


class VeeamJobButtonBase(CoordinatorEntity, ButtonEntity):
    """Base class for Veeam job buttons."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, config_entry, job_data, veeam_client):
        """Initialize the job button."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._job_id = job_data.get("id")
        self._job_name = job_data.get("name", "Unknown Job")
        self._veeam_client = veeam_client

    @property
    def device_info(self):
        """Return device info for this job."""
        return {
            "identifiers": {(DOMAIN, f"job_{self._job_id}")},
            "name": f"{self._job_name}",
            "manufacturer": "Veeam",
            "model": "Backup Job",
        }

    def _get_current_job_data(self) -> dict | None:
        """Find current job data from coordinator."""
        if not self.coordinator.data:
            return None
        for job in self.coordinator.data.get("jobs", []):
            if job.get("id") == self._job_id:
                return job
        return None

    @property
    def available(self) -> bool:
        """Return True if the job still exists in coordinator data."""
        return self._get_current_job_data() is not None

    def _get_api_module(self) -> str:
        """Get the API module name based on the configured API version."""
        api_version = self._config_entry.options.get(
            CONF_API_VERSION,
            self._config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
        )
        return API_VERSIONS.get(api_version, "v1_3_rev1")

    async def _import_spec_model(self, spec_name: str):
        """Import a spec model from the veeam_br library.

        Args:
            spec_name: Name of the spec model (e.g., 'job_start_spec', 'job_stop_spec')

        Returns:
            The spec model class

        Raises:
            ImportError: If the model cannot be imported
            AttributeError: If the model class cannot be found
        """
        api_module = self._get_api_module()
        models_module = await asyncio.to_thread(
            importlib.import_module, f"veeam_br.{api_module}.models.{spec_name}"
        )
        # Convert snake_case to PascalCase for class name
        class_name = "".join(word.capitalize() for word in spec_name.split("_"))
        return getattr(models_module, class_name)


class VeeamJobStartButton(VeeamJobButtonBase):
    """Button to start a Veeam job."""

    def __init__(self, coordinator, config_entry, job_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, job_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_start"
        self._attr_name = "Start"

    @property
    def available(self) -> bool:
        """Return True if the job is not currently running."""
        job = self._get_current_job_data()
        if job is None:
            return False
        return job.get("status", "").lower() not in _RUNNING_STATUSES

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:play"

    async def async_press(self) -> None:
        """Handle the button press to start the job."""
        try:
            JobStartSpec = await self._import_spec_model("job_start_spec")
            body = JobStartSpec(perform_active_full=False)
        except (ImportError, AttributeError) as e:
            _LOGGER.error("Failed to import JobStartSpec: %s. Cannot start job.", e)
            return

        try:
            jobs_api = await asyncio.to_thread(self._veeam_client.api, "jobs")
            result = await self._veeam_client.call(
                jobs_api.start_job,
                id=self._job_id,
                body=body,
            )
            if hasattr(result, "error_code"):
                _LOGGER.error(
                    "Veeam API error for job %s: %s",
                    self._job_name,
                    getattr(result, "message", "Unknown error"),
                )
                raise HomeAssistantError(f"Failed to start job '{self._job_name}'")
            _LOGGER.info("Successfully started job: %s", self._job_name)
            await self.coordinator.async_request_refresh()
        except HomeAssistantError:
            raise
        except Exception as err:
            _LOGGER.error("Failed to start job %s: %s", self._job_name, err)
            raise HomeAssistantError(
                f"Failed to start job '{self._job_name}'"
            ) from err


class VeeamJobStopButton(VeeamJobButtonBase):
    """Button to stop a Veeam job."""

    def __init__(self, coordinator, config_entry, job_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, job_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_stop"
        self._attr_name = "Stop"

    @property
    def available(self) -> bool:
        """Return True if the job is currently running."""
        job = self._get_current_job_data()
        if job is None:
            return False
        return job.get("status", "").lower() in _RUNNING_STATUSES

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:stop"

    async def async_press(self) -> None:
        """Handle the button press to stop the job."""
        try:
            JobStopSpec = await self._import_spec_model("job_stop_spec")
            body = JobStopSpec()
        except (ImportError, AttributeError) as e:
            _LOGGER.error("Failed to import JobStopSpec: %s. Cannot stop job.", e)
            return

        try:
            jobs_api = await asyncio.to_thread(self._veeam_client.api, "jobs")
            result = await self._veeam_client.call(
                jobs_api.stop_job,
                id=self._job_id,
                body=body,
            )
            if hasattr(result, "error_code"):
                _LOGGER.error(
                    "Veeam API error for job %s: %s",
                    self._job_name,
                    getattr(result, "message", "Unknown error"),
                )
                raise HomeAssistantError(f"Failed to stop job '{self._job_name}'")
            _LOGGER.info("Successfully stopped job: %s", self._job_name)
            await self.coordinator.async_request_refresh()
        except HomeAssistantError:
            raise
        except Exception as err:
            _LOGGER.error("Failed to stop job %s: %s", self._job_name, err)
            raise HomeAssistantError(
                f"Failed to stop job '{self._job_name}'"
            ) from err


class VeeamJobRetryButton(VeeamJobButtonBase):
    """Button to retry a failed Veeam job."""

    def __init__(self, coordinator, config_entry, job_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, job_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_retry"
        self._attr_name = "Retry"

    @property
    def available(self) -> bool:
        """Return True if the job is not running and last result indicates failure."""
        job = self._get_current_job_data()
        if job is None:
            return False
        status = job.get("status", "").lower()
        if status in _RUNNING_STATUSES:
            return False
        last_result = job.get("last_result", "").lower()
        return last_result in ("failed", "warning")

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:refresh"

    async def async_press(self) -> None:
        """Handle the button press to retry the job."""
        try:
            JobRetrySpec = await self._import_spec_model("job_retry_spec")
            body = JobRetrySpec()
        except (ImportError, AttributeError) as e:
            _LOGGER.error("Failed to import JobRetrySpec: %s. Cannot retry job.", e)
            return

        try:
            jobs_api = await asyncio.to_thread(self._veeam_client.api, "jobs")
            result = await self._veeam_client.call(
                jobs_api.retry_job,
                id=self._job_id,
                body=body,
            )
            if hasattr(result, "error_code"):
                _LOGGER.error(
                    "Veeam API error for job %s: %s",
                    self._job_name,
                    getattr(result, "message", "Unknown error"),
                )
                raise HomeAssistantError(f"Failed to retry job '{self._job_name}'")
            _LOGGER.info("Successfully retried job: %s", self._job_name)
            await self.coordinator.async_request_refresh()
        except HomeAssistantError:
            raise
        except Exception as err:
            _LOGGER.error("Failed to retry job %s: %s", self._job_name, err)
            raise HomeAssistantError(
                f"Failed to retry job '{self._job_name}'"
            ) from err


class VeeamJobEnableButton(VeeamJobButtonBase):
    """Button to enable a Veeam job."""

    def __init__(self, coordinator, config_entry, job_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, job_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_enable"
        self._attr_name = "Enable"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:check-circle-outline"

    async def async_press(self) -> None:
        """Handle the button press to enable the job."""
        try:
            jobs_api = await asyncio.to_thread(self._veeam_client.api, "jobs")
            await self._veeam_client.call(
                jobs_api.enable_job,
                id=self._job_id,
            )
            _LOGGER.info("Successfully enabled job: %s", self._job_name)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to enable job %s: %s", self._job_name, err)
            raise HomeAssistantError(
                f"Failed to enable job '{self._job_name}'"
            ) from err


class VeeamJobDisableButton(VeeamJobButtonBase):
    """Button to disable a Veeam job."""

    def __init__(self, coordinator, config_entry, job_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, job_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_disable"
        self._attr_name = "Disable"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:cancel"

    async def async_press(self) -> None:
        """Handle the button press to disable the job."""
        try:
            jobs_api = await asyncio.to_thread(self._veeam_client.api, "jobs")
            await self._veeam_client.call(
                jobs_api.disable_job,
                id=self._job_id,
            )
            _LOGGER.info("Successfully disabled job: %s", self._job_name)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to disable job %s: %s", self._job_name, err)
            raise HomeAssistantError(
                f"Failed to disable job '{self._job_name}'"
            ) from err
