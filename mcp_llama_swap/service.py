"""Service manager abstraction for managing llama-server processes."""

import asyncio
import logging
import sys
from abc import ABC, abstractmethod
from typing import Optional

import httpx

logger = logging.getLogger("mcp-llama-swap")


class ServiceManager(ABC):
    """Abstract interface for managing model service lifecycles.

    Implementations handle platform-specific details (launchctl on macOS,
    systemd on Linux) while exposing a uniform API.
    """

    # File extension for service configs (e.g., ".plist", ".service")
    file_extension: str = ""

    @abstractmethod
    def get_models(self, config: dict) -> dict[str, str]:
        """Return dict of alias -> service config file path.

        Two modes:
        - Mapped mode: config["models"] is a non-empty dict of alias -> filename
        - Directory mode: auto-discover all service files in the services directory
        """

    @abstractmethod
    def get_service_label(self, config_path: str) -> Optional[str]:
        """Extract the service identifier from a config file."""

    @abstractmethod
    async def is_loaded(self, label: str) -> bool:
        """Check if a service is currently running/loaded."""

    @abstractmethod
    async def load(self, config_path: str) -> tuple[bool, str]:
        """Load/start a service. Returns (success, error_message)."""

    @abstractmethod
    async def unload(self, config_path: str, label: str) -> tuple[bool, str]:
        """Unload/stop a service. Returns (success, error_message)."""

    @abstractmethod
    async def wait_for_unload(self, label: str, timeout: int = 10) -> bool:
        """Wait for a service to fully stop. Returns True if confirmed stopped."""

    @abstractmethod
    def create_service_config(
        self,
        name: str,
        llama_server_path: str,
        model_path: str,
        services_dir: str,
        context_size: int = 4096,
        gpu_layers: int = -1,
        port: int = 8000,
    ) -> str:
        """Generate and save a service config file. Returns the file path."""

    # --- Shared higher-level methods ---

    async def get_loaded_models(self, models: dict[str, str]) -> set[str]:
        """Get aliases whose services are currently loaded."""
        loaded = set()
        for alias, path in models.items():
            label = self.get_service_label(path)
            if label and await self.is_loaded(label):
                loaded.add(alias)
        return loaded

    async def unload_all(
        self, models: dict[str, str]
    ) -> tuple[list[str], list[str]]:
        """Unload all model services.

        Returns:
            Tuple of (successfully_unloaded, failed_to_unload) alias lists.
        """
        unloaded = []
        failed = []
        for alias, path in models.items():
            label = self.get_service_label(path)
            if label and await self.is_loaded(label):
                success, msg = await self.unload(path, label)
                if success:
                    logger.info("Unloaded model '%s' (label: %s)", alias, label)
                    unloaded.append(alias)
                else:
                    logger.error("Failed to unload '%s': %s", alias, msg)
                    failed.append(alias)
        return unloaded, failed


async def wait_for_health(url: str, timeout: int) -> bool:
    """Poll llama-server health endpoint until it responds 200."""
    logger.info("Waiting for health at %s (timeout: %ds)", url, timeout)
    async with httpx.AsyncClient() as client:
        for i in range(timeout):
            try:
                resp = await client.get(url, timeout=2.0)
                if resp.status_code == 200:
                    logger.info("Health check passed after %ds", i + 1)
                    return True
            except (httpx.ConnectError, httpx.ReadTimeout):
                pass
            await asyncio.sleep(1)
    logger.warning("Health check timed out after %ds at %s", timeout, url)
    return False


def get_service_manager(config: dict) -> ServiceManager:
    """Create the appropriate ServiceManager for the current platform.

    Config keys:
        platform: "auto" (default), "launchctl", or "systemd"
        launchctl_mode: "legacy" (default) or "modern" — macOS only
    """
    from mcp_llama_swap.launchctl import LaunchctlManager
    from mcp_llama_swap.systemd import SystemdManager

    platform = config.get("platform", "auto")

    if platform == "auto":
        if sys.platform == "darwin":
            mode = config.get("launchctl_mode", "legacy")
            return LaunchctlManager(modern=(mode == "modern"))
        elif sys.platform == "linux":
            return SystemdManager()
        else:
            raise RuntimeError(
                f"Unsupported platform: {sys.platform}. "
                "Set 'platform' in config to 'launchctl' or 'systemd'."
            )
    elif platform == "launchctl":
        mode = config.get("launchctl_mode", "legacy")
        return LaunchctlManager(modern=(mode == "modern"))
    elif platform == "systemd":
        return SystemdManager()
    else:
        raise RuntimeError(
            f"Unknown platform '{platform}'. Use 'auto', 'launchctl', or 'systemd'."
        )
