"""Token management for Veeam Backup & Replication integration."""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


class VeeamTokenManager:
    """Manage Veeam API tokens with automatic refresh using VeeamClient."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        verify_ssl: bool = True,
        api_version: str = "1.3-rev1",
    ):
        """Initialize the token manager.

        Args:
            base_url: Base URL of the Veeam server (e.g., https://veeam.example.com:9419)
            username: Veeam username for authentication
            password: Veeam password for authentication
            verify_ssl: Whether to verify SSL certificates
            api_version: API version to use
        """
        self._base_url = base_url
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._api_version = api_version
        self._veeam_client = None
        self._is_connected = False

    def _get_veeam_client(self):
        """Get or create the VeeamClient instance.

        Returns:
            VeeamClient: The VeeamClient instance
        """
        if self._veeam_client is None:
            from veeam_br.client import VeeamClient

            self._veeam_client = VeeamClient(
                host=self._base_url,
                username=self._username,
                password=self._password,
                api_version=self._api_version,
                verify_ssl=self._verify_ssl,
            )
        return self._veeam_client

    async def ensure_valid_token(self, hass) -> bool:
        """Ensure we have a valid connection and token.

        Args:
            hass: Home Assistant instance

        Returns:
            bool: True if connected and authenticated
        """
        try:
            if not self._is_connected:
                client = self._get_veeam_client()
                await client.connect()
                self._is_connected = True
                _LOGGER.debug("Successfully connected to Veeam API")
            return True
        except Exception as err:
            _LOGGER.error("Failed to connect to Veeam API: %s", err)
            self._is_connected = False
            return False

    def get_veeam_client(self):
        """Get the VeeamClient for API calls.

        Returns:
            VeeamClient: The VeeamClient instance
        """
        return self._get_veeam_client()

    def get_token_info(self) -> dict[str, Any]:
        """Get current token information for debugging.

        Returns:
            dict: Token information
        """
        client = self._get_veeam_client()
        return {
            "is_connected": self._is_connected,
            "has_access_token": bool(client._access_token) if client else False,
            "has_refresh_token": bool(client._refresh_token) if client else False,
            "expires_at": client._expires_at.isoformat() if client and client._expires_at else None,
        }
