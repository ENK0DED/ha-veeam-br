"""Constants for the Veeam Backup & Replication integration."""

DOMAIN = "veeam_br"
DEFAULT_NAME = "Veeam Backup & Replication"

# Configuration keys
CONF_VERIFY_SSL = "verify_ssl"
CONF_API_VERSION = "api_version"

# Defaults
DEFAULT_PORT = 9419
DEFAULT_VERIFY_SSL = True
DEFAULT_API_VERSION = "1.3-rev1"

# API Version options
API_VERSIONS = {
    "1.2-rev1": "v1_2_rev1",
    "1.3-rev0": "v1_3_rev0",
    "1.3-rev1": "v1_3_rev1",
}

# Update interval
UPDATE_INTERVAL = 60  # seconds
