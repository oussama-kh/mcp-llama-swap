"""MCP server for hot-swapping llama.cpp models."""

import json
import logging
import os
import shutil
import sys

from mcp.server.fastmcp import FastMCP

from mcp_llama_swap.config import (
    get_health_timeout,
    get_health_url,
    load_config,
    resolve_services_dir,
)
from mcp_llama_swap.service import get_service_manager, wait_for_health

logger = logging.getLogger("mcp-llama-swap")

platform_hint = "launchctl" if sys.platform == "darwin" else "systemd"

mcp = FastMCP(
    "llama-swap",
    instructions=(
        f"Server for hot-swapping llama.cpp models via {platform_hint}. "
        "Use list_models to see available models, get_current_model to check "
        "which is loaded, and swap_model to switch. Use create_model_config "
        "to generate new model service configs. Model swaps preserve "
        "conversation context."
    ),
)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_models() -> str:
    """List available llama.cpp model configurations and their load status."""
    config = load_config()
    mgr = get_service_manager(config)
    models = mgr.get_models(config)

    if not models:
        services_dir = resolve_services_dir(config)
        return f"No models found. Check services directory ({services_dir}) and config."

    loaded = await mgr.get_loaded_models(models)
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
    config = load_config()
    mgr = get_service_manager(config)
    models = mgr.get_models(config)
    loaded = await mgr.get_loaded_models(models)

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
    config = load_config()
    mgr = get_service_manager(config)
    models = mgr.get_models(config)
    health_url = get_health_url(config)
    health_timeout = get_health_timeout(config)

    if model not in models:
        available = ", ".join(sorted(models.keys()))
        return f"Model '{model}' not found. Available: {available}"

    loaded = await mgr.get_loaded_models(models)
    if model in loaded and len(loaded) == 1:
        healthy = await wait_for_health(health_url, timeout=5)
        if healthy:
            return f"Model '{model}' is already loaded and healthy"

    logger.info("Swapping to model '%s'", model)
    unloaded, failed = await mgr.unload_all(models)

    if failed:
        logger.warning("Some models failed to unload: %s", failed)

    # Wait for unloaded services to fully stop
    for alias in unloaded:
        label = mgr.get_service_label(models[alias])
        if label:
            await mgr.wait_for_unload(label)

    path = models[model]
    success, err = await mgr.load(path)

    if not success:
        return f"Failed to load '{model}': {err}"

    logger.info("Loaded model '%s', waiting for health check", model)
    healthy = await wait_for_health(health_url, health_timeout)
    if healthy:
        return f"Model '{model}' loaded and ready"
    else:
        msg = (
            f"Model '{model}' loaded but health check timed out after "
            f"{health_timeout}s. Server may still be loading weights."
        )
        if failed:
            msg += (
                f" Warning: failed to unload {', '.join(failed)} "
                "— port may be in use."
            )
        return msg


@mcp.tool()
async def create_model_config(
    name: str,
    model_path: str,
    context_size: int = 4096,
    gpu_layers: int = -1,
    port: int = 8000,
) -> str:
    """Generate a new service config for a llama-server model.

    Creates a launchd plist (macOS) or systemd unit (Linux) that can
    be used with swap_model.

    Args:
        name: Short alias for the model (e.g., "coder", "planner")
        model_path: Absolute path to the GGUF model file
        context_size: Context window size (default: 4096)
        gpu_layers: Number of GPU layers, -1 for all (default: -1)
        port: Port for llama-server (default: 8000)
    """
    if not os.path.isabs(model_path):
        return f"model_path must be an absolute path, got: {model_path}"

    if not os.path.isfile(model_path):
        return f"Model file not found: {model_path}"

    llama_server = shutil.which("llama-server")
    if llama_server is None:
        # Try common locations
        for candidate in [
            "/usr/local/bin/llama-server",
            "/opt/homebrew/bin/llama-server",
            os.path.expanduser("~/llama.cpp/build/bin/llama-server"),
        ]:
            if os.path.isfile(candidate):
                llama_server = candidate
                break

    if llama_server is None:
        return (
            "Could not find llama-server binary. Install llama.cpp or "
            "provide llama-server in your PATH."
        )

    config = load_config()
    mgr = get_service_manager(config)
    services_dir = resolve_services_dir(config)

    path = mgr.create_service_config(
        name=name,
        llama_server_path=llama_server,
        model_path=model_path,
        services_dir=services_dir,
        context_size=context_size,
        gpu_layers=gpu_layers,
        port=port,
    )

    return (
        f"Created service config at {path}\n"
        f"You can now add it to your config.json models map as:\n"
        f'  "{name}": "{os.path.basename(path)}"\n'
        f"Or use directory mode to auto-discover it."
    )


# ---------------------------------------------------------------------------
# MCP Resources
# ---------------------------------------------------------------------------


@mcp.resource("llama-swap://config")
async def config_resource() -> str:
    """Current llama-swap configuration."""
    return json.dumps(load_config(), indent=2)


@mcp.resource("llama-swap://status")
async def status_resource() -> str:
    """Current model status and health."""
    config = load_config()
    mgr = get_service_manager(config)
    models = mgr.get_models(config)
    loaded = await mgr.get_loaded_models(models)
    health_url = get_health_url(config)

    import httpx

    status = {
        "loaded_models": sorted(loaded),
        "available_models": sorted(models.keys()),
        "health_url": health_url,
        "mode": "mapped" if config.get("models") else "directory",
        "platform": "launchctl" if sys.platform == "darwin" else "systemd",
    }

    if loaded:
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(health_url, timeout=2.0)
                status["health"] = (
                    "ok"
                    if resp.status_code == 200
                    else f"status {resp.status_code}"
                )
            except (httpx.ConnectError, httpx.ReadTimeout):
                status["health"] = "unreachable"
    else:
        status["health"] = "no model loaded"

    return json.dumps(status, indent=2)


# ---------------------------------------------------------------------------
# MCP Prompts
# ---------------------------------------------------------------------------


@mcp.prompt("swap-workflow")
async def swap_workflow() -> str:
    """Guided workflow for planning with a reasoning model, then implementing with a coding model."""
    config = load_config()
    mgr = get_service_manager(config)
    models = mgr.get_models(config)
    model_list = ", ".join(sorted(models.keys())) if models else "(none configured)"
    return (
        f"Available models: {model_list}\n\n"
        "Recommended workflow:\n"
        "1. Use list_models to see available models and their status\n"
        "2. Swap to a reasoning/planning model for architecture discussion\n"
        "3. Define the plan, interfaces, and requirements\n"
        "4. Swap to a coding model for implementation\n"
        "5. Implement the plan with full context from the planning phase\n"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Entry point for console_scripts."""
    mcp.run()
