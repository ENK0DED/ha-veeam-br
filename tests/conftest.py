"""Common fixtures for Veeam Backup & Replication tests."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(name="mock_veeam_client")
def mock_veeam_client_fixture():
    """Mock VeeamClient."""
    with patch("custom_components.veeam_br.config_flow.VeeamClient") as mock_client:
        client_instance = MagicMock()
        client_instance.connect = AsyncMock(return_value=None)
        mock_client.return_value = client_instance
        yield mock_client


@pytest.fixture(name="mock_setup_entry")
def mock_setup_entry_fixture():
    """Mock setup entry."""
    with patch(
        "custom_components.veeam_br.async_setup_entry",
        return_value=True,
    ) as mock_setup:
        yield mock_setup
