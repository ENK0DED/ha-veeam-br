"""Basic validation tests for Veeam BR integration."""

import pytest


def test_manifest_valid():
    """Test that manifest.json is valid and contains required fields."""
    import json
    from pathlib import Path

    manifest_path = (
        Path(__file__).parent.parent / "custom_components" / "veeam_br" / "manifest.json"
    )

    with open(manifest_path) as f:
        manifest = json.load(f)

    # Check required fields
    required_fields = [
        "domain",
        "name",
        "version",
        "documentation",
        "requirements",
        "codeowners",
        "iot_class",
        "config_flow",
        "quality_scale",
    ]
    for field in required_fields:
        assert field in manifest, f"Missing required field: {field}"

    # Check specific values
    assert manifest["domain"] == "veeam_br"
    assert manifest["config_flow"] is True
    assert manifest["quality_scale"] == "bronze"
    assert "veeam-br" in manifest["requirements"][0]


def test_strings_valid():
    """Test that strings.json is valid."""
    import json
    from pathlib import Path

    strings_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "strings.json"

    with open(strings_path) as f:
        strings = json.load(f)

    # Check for required sections
    assert "config" in strings
    assert "options" in strings

    # Check for reauth support
    assert "reauth_confirm" in strings["config"]["step"]
    assert "username" in strings["config"]["step"]["reauth_confirm"]["data"]
    assert "password" in strings["config"]["step"]["reauth_confirm"]["data"]


def test_imports():
    """Test that all modules can be imported."""
    from custom_components.veeam_br import config_flow, const

    # Check constants are defined
    assert hasattr(const, "DOMAIN")
    assert hasattr(const, "DEFAULT_PORT")
    assert hasattr(const, "CONF_API_VERSION")

    # Check config flow class exists
    assert hasattr(config_flow, "VeeamBRConfigFlow")
    assert hasattr(config_flow.VeeamBRConfigFlow, "async_step_reauth")
    assert hasattr(config_flow.VeeamBRConfigFlow, "async_step_reauth_confirm")


def test_const_api_versions():
    """Test that API versions are properly configured."""
    from custom_components.veeam_br.const import API_VERSIONS, DEFAULT_API_VERSION

    # Check that API versions dict is not empty
    assert isinstance(API_VERSIONS, dict)
    assert len(API_VERSIONS) > 0

    # Check default API version is in the list
    assert DEFAULT_API_VERSION in API_VERSIONS


def test_config_flow_has_reauth():
    """Test that config flow has reauth capability."""
    import inspect

    from custom_components.veeam_br.config_flow import VeeamBRConfigFlow

    # Check that reauth methods exist
    assert hasattr(VeeamBRConfigFlow, "async_step_reauth")
    assert hasattr(VeeamBRConfigFlow, "async_step_reauth_confirm")

    # Check they are async methods
    assert inspect.iscoroutinefunction(VeeamBRConfigFlow.async_step_reauth)
    assert inspect.iscoroutinefunction(VeeamBRConfigFlow.async_step_reauth_confirm)


def test_runtime_data_usage():
    """Test that the integration uses runtime_data instead of hass.data."""
    from pathlib import Path

    init_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "__init__.py"

    with open(init_path) as f:
        init_content = f.read()

    # Check that runtime_data is used
    assert "entry.runtime_data" in init_content, "Integration should use entry.runtime_data"

    # Check for sensor.py
    sensor_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "sensor.py"
    with open(sensor_path) as f:
        sensor_content = f.read()

    assert "entry.runtime_data" in sensor_content, "Sensors should use entry.runtime_data"

    # Check for button.py
    button_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "button.py"
    with open(button_path) as f:
        button_content = f.read()

    assert "entry.runtime_data" in button_content, "Buttons should use entry.runtime_data"
