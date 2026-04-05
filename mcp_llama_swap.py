#!/usr/bin/env python3
"""MCP server for hot-swapping llama.cpp models via macOS launchctl."""

import asyncio
import glob
import json
import os
import plistlib
import subprocess
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP

CONFIG_PATH = os.environ.get(
    "LLAMA_SWAP_CONFIG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"),
)

mcp = FastMCP("llama-swap")


def _load_config() -> dict:
    """Load configuration from JSON file."""
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _resolve_plists_dir(config: dict) -> str:
    raw = config.get("plists_dir", "~/.llama-plists")
    return os.path.expanduser(raw)


def _get_health_url(config: dict) -> str:
    return config.get("health_url", "http://localhost:8000/health")


def _get_health_timeout(config: dict) -> int:
    return int(config.get("health_timeout", 30))


def _get_models(config: dict) -> dict[str, str]:
    """Return dict of alias -> absolute plist path.

    Two modes controlled by the "models" key in config:

    Mapped mode (models is a non-empty dict):
        Keys are aliases you choose, values are plist filenames.
        Only mapped models are available. Unmapped plists in the
        directory are ignored.

    Directory mode (models is absent, null, or empty):
        All .plist files in plists_dir are discovered. Filenames
        without extension become the aliases.
    """
    plists_dir = _resolve_plists_dir(config)
    model_map = config.get("models")

    if model_map:
        resolved = {}
        for alias, filename in model_map.items():
            path = os.path.join(plists_dir, filename)
            if os.path.isfile(path):
                resolved[alias] = path
        return resolved

    models = {}
    for path in glob.glob(os.path.join(plists_dir, "*.plist")):
        name = os.path.splitext(os.path.basename(path))[0]
        models[name] = path
    return models


def _get_plist_label(path: str) -> Optional[str]:
    """Extract the Label from a plist file."""
    try:
        with open(path, "rb") as f:
            data = plistlib.load(f)
        return data.get("Label")
    except Exception:
        return None


def _get_loaded_models(models: dict[str, str]) -> set[str]:
    """Get aliases whose launchctl jobs are currently loaded."""
    loaded = set()
    for alias, path in models.items():
        label = _get_plist_label(path)
        if label:
            result = subprocess.run(
                ["launchctl", "list", label],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                loaded.add(alias)
    return loaded


def _unload_all(models: dict[str, str]) -> list[str]:
    """Unload all known model plists. Returns list of unloaded aliases."""
    unloaded = []
    for alias, path in models.items():
        label = _get_plist_label(path)
        if label:
            check = subprocess.run(
                ["launchctl", "list", label],
                capture_output=True,
                text=True,
            )
            if check.returncode == 0:
                subprocess.run(
                    ["launchctl", "unload", path],
                    capture_output=True,
                    text=True,
                )
                unloaded.append(alias)
    return unloaded


async def _wait_for_health(url: str, timeout: int) -> bool:
    """Poll llama-server health endpoint until it responds 200."""
    async with httpx.AsyncClient() as client:
        for _ in range(timeout):
            try:
                resp = await client.get(url, timeout=2.0)
                if resp.status_code == 200:
                    return True
            except (httpx.ConnectError, httpx.ReadTimeout):
                pass
            await asyncio.sleep(1)
    return False


@mcp.tool()
async def list_models() -> str:
    """List available llama.cpp model configurations and their load status."""
    config = _load_config()
    models = _get_models(config)

    if not models:
        plists_dir = _resolve_plists_dir(config)
        return f"No models found. Check plists_dir ({plists_dir}) and config."

    loaded = _get_loaded_models(models)
    mode = "mapped" if config.get("models") else "directory"
    lines = [f"Mode: {mode}"]

    for alias in sorted(models.keys()):
        status = "LOADED" if alias in loaded else "available"
        filename = os.path.basename(models[alias])
        if mode == "mapped":
            lines.append(f"  {alias} -> {filename}: {status}")
        else:
            lines.append(f"  {alias}: {status}")

    return "\n".join(lines)


@mcp.tool()
async def get_current_model() -> str:
    """Get the currently loaded llama.cpp model."""
    config = _load_config()
    models = _get_models(config)
    loaded = _get_loaded_models(models)

    if not loaded:
        return "No model currently loaded"
    return f"Currently loaded: {', '.join(sorted(loaded))}"


@mcp.tool()
async def swap_model(model: str) -> str:
    """Swap to a different llama.cpp model.

    Unloads any currently loaded model, loads the requested one,
    and waits for the health endpoint to confirm readiness.

    Args:
        model: Alias of the model to load
    """
    config = _load_config()
    models = _get_models(config)
    health_url = _get_health_url(config)
    health_timeout = _get_health_timeout(config)

    if model not in models:
        available = ", ".join(sorted(models.keys()))
        return f"Model '{model}' not found. Available: {available}"

    loaded = _get_loaded_models(models)
    if model in loaded and len(loaded) == 1:
        healthy = await _wait_for_health(health_url, timeout=5)
        if healthy:
            return f"Model '{model}' is already loaded and healthy"

    unloaded = _unload_all(models)
    if unloaded:
        await asyncio.sleep(2)

    path = models[model]
    result = subprocess.run(
        ["launchctl", "load", path],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return f"Failed to load '{model}': {result.stderr.strip()}"

    healthy = await _wait_for_health(health_url, health_timeout)
    if healthy:
        return f"Model '{model}' loaded and ready"
    else:
        return (
            f"Model '{model}' loaded but health check timed out after "
            f"{health_timeout}s. Server may still be loading weights."
        )


def main():
    """Entry point for console_scripts."""
    mcp.run()


if __name__ == "__main__":
    main()
