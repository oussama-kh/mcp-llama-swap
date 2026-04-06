"""Configuration loading and validation."""

import json
import logging
import os

logger = logging.getLogger("mcp-llama-swap")


def load_config() -> dict:
    """Load configuration from JSON file."""
    config_path = os.environ.get(
        "LLAMA_SWAP_CONFIG",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"),
    )
    try:
        with open(config_path) as f:
            config = json.load(f)
        logger.info("Loaded config from %s", config_path)
        validate_config(config)
        return config
    except FileNotFoundError:
        logger.warning("Config file not found: %s", config_path)
        return {}
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in config %s: %s", config_path, e)
        return {}


def validate_config(config: dict) -> list[str]:
    """Validate config and log warnings for any issues. Returns list of warnings."""
    warnings: list[str] = []

    services_dir = resolve_services_dir(config)
    if not os.path.isdir(services_dir):
        msg = f"services directory does not exist: {services_dir}"
        warnings.append(msg)
        logger.warning(msg)

    health_url = config.get("health_url", "http://localhost:8000/health")
    if not health_url.startswith(("http://", "https://")):
        msg = f"health_url is not a valid URL: {health_url}"
        warnings.append(msg)
        logger.warning(msg)

    raw_timeout = config.get("health_timeout", 30)
    try:
        timeout = int(raw_timeout)
        if timeout <= 0:
            msg = f"health_timeout must be positive, got {timeout}"
            warnings.append(msg)
            logger.warning(msg)
    except (ValueError, TypeError):
        msg = f"health_timeout is not a valid integer: {raw_timeout}"
        warnings.append(msg)
        logger.warning(msg)

    model_map = config.get("models")
    if model_map and isinstance(model_map, dict):
        for alias, filename in model_map.items():
            path = os.path.join(services_dir, filename)
            if not os.path.isfile(path):
                msg = f"Model '{alias}' config not found: {path}"
                warnings.append(msg)
                logger.warning(msg)

    return warnings


def resolve_services_dir(config: dict) -> str:
    """Resolve the directory containing service config files.

    Checks keys in order: services_dir, plists_dir (macOS compat),
    units_dir (Linux compat). Falls back to platform default.
    """
    import sys

    raw = config.get("services_dir")
    if raw is None:
        raw = config.get("plists_dir")  # macOS backwards compat
    if raw is None:
        raw = config.get("units_dir")  # Linux alias
    if raw is None:
        if sys.platform == "darwin":
            raw = "~/.llama-plists"
        else:
            raw = "~/.llama-services"
    return os.path.expanduser(raw)


def get_health_url(config: dict) -> str:
    return config.get("health_url", "http://localhost:8000/health")


def get_health_timeout(config: dict) -> int:
    try:
        timeout = int(config.get("health_timeout", 30))
        return max(timeout, 1)
    except (ValueError, TypeError):
        logger.warning("Invalid health_timeout, using default 30s")
        return 30
