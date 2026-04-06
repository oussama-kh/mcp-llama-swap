"""Linux systemd service manager."""

import asyncio
import glob
import logging
import os
import re
from typing import Optional

from mcp_llama_swap.config import resolve_services_dir
from mcp_llama_swap.service import ServiceManager

logger = logging.getLogger("mcp-llama-swap")


class SystemdManager(ServiceManager):
    """Manage llama-server processes via systemd user services on Linux."""

    file_extension = ".service"

    def __init__(self):
        logger.info("SystemdManager initialized")

    # --- Internal helpers ---

    async def _run_systemctl(self, *args: str) -> tuple[int, str, str]:
        """Run a systemctl --user command asynchronously."""
        cmd = ("systemctl", "--user", *args)
        logger.debug("Running: %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        return proc.returncode, stdout_bytes.decode(), stderr_bytes.decode()

    async def _daemon_reload(self) -> None:
        """Reload systemd user daemon to pick up new/changed unit files."""
        rc, _, stderr = await self._run_systemctl("daemon-reload")
        if rc != 0:
            logger.warning("daemon-reload failed: %s", stderr.strip())

    def _unit_dir(self) -> str:
        """Return the systemd user unit directory."""
        xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        return os.path.join(xdg, "systemd", "user")

    def _ensure_unit_linked(self, config_path: str) -> str:
        """Ensure the unit file is accessible to systemd.

        If config_path is outside the systemd user unit directory, symlink
        it there so systemctl can find it.

        Returns the unit name (e.g., 'llama-server-coder.service').
        """
        unit_name = os.path.basename(config_path)
        unit_dir = self._unit_dir()
        target = os.path.join(unit_dir, unit_name)

        if os.path.abspath(config_path) != os.path.abspath(target):
            os.makedirs(unit_dir, exist_ok=True)
            if os.path.lexists(target):
                os.unlink(target)
            os.symlink(os.path.abspath(config_path), target)
            logger.debug("Symlinked %s -> %s", config_path, target)

        return unit_name

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
                        "Skipping model '%s': unit file not found at %s",
                        alias,
                        path,
                    )
            return resolved

        models = {}
        for path in glob.glob(os.path.join(services_dir, "*.service")):
            name = os.path.splitext(os.path.basename(path))[0]
            models[name] = path
        logger.debug(
            "Discovered %d models in directory mode from %s",
            len(models),
            services_dir,
        )
        return models

    def get_service_label(self, config_path: str) -> Optional[str]:
        """Extract unit name from the file path (the filename is the label)."""
        if not os.path.isfile(config_path):
            logger.warning("Unit file not found: %s", config_path)
            return None
        return os.path.basename(config_path)

    async def is_loaded(self, label: str) -> bool:
        rc, stdout, _ = await self._run_systemctl("is-active", label)
        return rc == 0 and stdout.strip() == "active"

    async def load(self, config_path: str) -> tuple[bool, str]:
        unit_name = self._ensure_unit_linked(config_path)
        await self._daemon_reload()
        rc, _, stderr = await self._run_systemctl("start", unit_name)
        return rc == 0, stderr.strip()

    async def unload(self, config_path: str, label: str) -> tuple[bool, str]:
        rc, _, stderr = await self._run_systemctl("stop", label)
        return rc == 0, stderr.strip()

    async def wait_for_unload(self, label: str, timeout: int = 10) -> bool:
        for i in range(timeout):
            if not await self.is_loaded(label):
                logger.debug(
                    "Service '%s' confirmed stopped after %ds", label, i
                )
                return True
            await asyncio.sleep(1)
        logger.warning("Service '%s' still active after %ds", label, timeout)
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
        unit_content = f"""\
[Unit]
Description=llama-server: {name}
After=network.target

[Service]
Type=simple
ExecStart={llama_server_path} \\
    -m {model_path} \\
    -c {context_size} \\
    -ngl {gpu_layers} \\
    --port {port}
Restart=no
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""
        os.makedirs(services_dir, exist_ok=True)
        unit_name = f"llama-server-{name}.service"
        unit_path = os.path.join(services_dir, unit_name)
        with open(unit_path, "w") as f:
            f.write(unit_content)

        logger.info("Created systemd unit at %s", unit_path)
        return unit_path
