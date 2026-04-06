"""macOS launchctl service manager — supports both legacy and modern commands."""

import asyncio
import functools
import glob
import logging
import os
import plistlib
import shutil
from typing import Optional

from mcp_llama_swap.config import resolve_services_dir
from mcp_llama_swap.service import ServiceManager

logger = logging.getLogger("mcp-llama-swap")


# ---------------------------------------------------------------------------
# Plist label cache (module-level for lru_cache compatibility)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=32)
def _get_plist_label_cached(path: str, mtime: float) -> Optional[str]:
    """Extract the Label from a plist file (cached by path+mtime)."""
    try:
        with open(path, "rb") as f:
            data = plistlib.load(f)
        label = data.get("Label")
        if label is None:
            logger.warning("Plist has no Label key: %s", path)
        return label
    except FileNotFoundError:
        logger.warning("Plist file not found: %s", path)
        return None
    except plistlib.InvalidFileException as e:
        logger.error("Malformed plist file %s: %s", path, e)
        return None


# ---------------------------------------------------------------------------
# LaunchctlManager
# ---------------------------------------------------------------------------


class LaunchctlManager(ServiceManager):
    """Manage llama-server processes via macOS launchctl.

    Supports two modes:
    - legacy (default): uses ``launchctl load/unload/list`` (deprecated but
      widely supported on all macOS versions).
    - modern: uses ``launchctl bootstrap/bootout/print`` (the officially
      supported API on newer macOS versions).
    """

    file_extension = ".plist"

    def __init__(self, modern: bool = False):
        self.modern = modern
        self._uid = os.getuid()
        self._domain = f"gui/{self._uid}"
        mode_label = "modern" if modern else "legacy"
        logger.info("LaunchctlManager initialized (mode: %s)", mode_label)

    # --- Internal helpers ---

    async def _run(self, *args: str) -> tuple[int, str, str]:
        """Run a launchctl command asynchronously."""
        logger.debug("Running: launchctl %s", " ".join(args))
        proc = await asyncio.create_subprocess_exec(
            "launchctl",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        return proc.returncode, stdout_bytes.decode(), stderr_bytes.decode()

    # --- ServiceManager interface ---

    def get_models(self, config: dict) -> dict[str, str]:
        services_dir = resolve_services_dir(config)
        model_map = config.get("models")

        if model_map:
            resolved = {}
            for alias, filename in model_map.items():
                path = os.path.join(services_dir, filename)
                if os.path.isfile(path):
                    resolved[alias] = path
                else:
                    logger.warning(
                        "Skipping model '%s': plist not found at %s", alias, path
                    )
            return resolved

        models = {}
        for path in glob.glob(os.path.join(services_dir, "*.plist")):
            name = os.path.splitext(os.path.basename(path))[0]
            models[name] = path
        logger.debug(
            "Discovered %d models in directory mode from %s",
            len(models),
            services_dir,
        )
        return models

    def get_service_label(self, config_path: str) -> Optional[str]:
        try:
            mtime = os.path.getmtime(config_path)
        except OSError:
            logger.warning("Cannot stat plist file: %s", config_path)
            return None
        return _get_plist_label_cached(config_path, mtime)

    async def is_loaded(self, label: str) -> bool:
        if self.modern:
            rc, _, _ = await self._run("print", f"{self._domain}/{label}")
        else:
            rc, _, _ = await self._run("list", label)
        return rc == 0

    async def load(self, config_path: str) -> tuple[bool, str]:
        if self.modern:
            rc, _, stderr = await self._run(
                "bootstrap", self._domain, config_path
            )
        else:
            rc, _, stderr = await self._run("load", config_path)
        return rc == 0, stderr.strip()

    async def unload(self, config_path: str, label: str) -> tuple[bool, str]:
        if self.modern:
            rc, _, stderr = await self._run(
                "bootout", f"{self._domain}/{label}"
            )
        else:
            rc, _, stderr = await self._run("unload", config_path)
        return rc == 0, stderr.strip()

    async def wait_for_unload(self, label: str, timeout: int = 10) -> bool:
        for i in range(timeout):
            if not await self.is_loaded(label):
                logger.debug(
                    "Service '%s' confirmed unloaded after %ds", label, i
                )
                return True
            await asyncio.sleep(1)
        logger.warning("Service '%s' still loaded after %ds", label, timeout)
        return False

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
        label = f"com.llama-server.{name}"
        plist_data = {
            "Label": label,
            "ProgramArguments": [
                llama_server_path,
                "-m",
                model_path,
                "-c",
                str(context_size),
                "-ngl",
                str(gpu_layers),
                "--port",
                str(port),
            ],
            "RunAtLoad": False,
            "KeepAlive": False,
            "StandardOutPath": f"/tmp/llama-server-{name}.stdout.log",
            "StandardErrorPath": f"/tmp/llama-server-{name}.stderr.log",
            "EnvironmentVariables": {
                "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin",
            },
        }

        os.makedirs(services_dir, exist_ok=True)
        plist_path = os.path.join(services_dir, f"{label}.plist")
        with open(plist_path, "wb") as f:
            plistlib.dump(plist_data, f)

        logger.info("Created plist at %s", plist_path)
        return plist_path
