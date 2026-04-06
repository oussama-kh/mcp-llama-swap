"""Tests for mcp-llama-swap."""

import json
import os
import plistlib
import sys
from unittest.mock import AsyncMock, patch

import pytest

from mcp_llama_swap.config import (
    get_health_timeout,
    load_config,
    resolve_services_dir,
    validate_config,
)
from mcp_llama_swap.launchctl import LaunchctlManager, _get_plist_label_cached
from mcp_llama_swap.service import get_service_manager, wait_for_health
from mcp_llama_swap.systemd import SystemdManager
from mcp_llama_swap import server


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config_dir(tmp_path):
    """Create a temp directory with plists and a config file."""
    plists_dir = tmp_path / "plists"
    plists_dir.mkdir()

    for name, label in [("coder", "com.test.coder"), ("planner", "com.test.planner")]:
        plist_path = plists_dir / f"{name}.plist"
        plist_data = {"Label": label, "ProgramArguments": ["/usr/bin/true"]}
        with open(plist_path, "wb") as f:
            plistlib.dump(plist_data, f)

    config = {
        "plists_dir": str(plists_dir),
        "health_url": "http://localhost:9999/health",
        "health_timeout": 5,
        "models": {
            "coder": "coder.plist",
            "planner": "planner.plist",
        },
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))

    return tmp_path, config_path, plists_dir


@pytest.fixture
def set_config_env(config_dir, monkeypatch):
    """Set LLAMA_SWAP_CONFIG env var to the temp config."""
    _, config_path, _ = config_dir
    monkeypatch.setenv("LLAMA_SWAP_CONFIG", str(config_path))
    _get_plist_label_cached.cache_clear()
    return config_dir


@pytest.fixture
def systemd_config_dir(tmp_path):
    """Create a temp directory with systemd unit files and a config file."""
    services_dir = tmp_path / "services"
    services_dir.mkdir()

    for name in ["coder", "planner"]:
        unit_path = services_dir / f"llama-server-{name}.service"
        unit_path.write_text(
            f"[Unit]\nDescription=llama-server: {name}\n\n"
            f"[Service]\nType=simple\nExecStart=/usr/bin/true\n"
        )

    config = {
        "platform": "systemd",
        "services_dir": str(services_dir),
        "health_url": "http://localhost:9999/health",
        "health_timeout": 5,
        "models": {
            "coder": f"llama-server-coder.service",
            "planner": f"llama-server-planner.service",
        },
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))

    return tmp_path, config_path, services_dir


@pytest.fixture
def set_systemd_config_env(systemd_config_dir, monkeypatch):
    """Set LLAMA_SWAP_CONFIG env var for systemd config."""
    _, config_path, _ = systemd_config_dir
    monkeypatch.setenv("LLAMA_SWAP_CONFIG", str(config_path))
    return systemd_config_dir


# ---------------------------------------------------------------------------
# Config loading tests
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_loads_valid_config(self, set_config_env):
        config = load_config()
        assert config["health_timeout"] == 5
        assert "coder" in config["models"]

    def test_missing_config_returns_empty(self, monkeypatch):
        monkeypatch.setenv("LLAMA_SWAP_CONFIG", "/nonexistent/path/config.json")
        config = load_config()
        assert config == {}

    def test_invalid_json_returns_empty(self, tmp_path, monkeypatch):
        bad_config = tmp_path / "bad.json"
        bad_config.write_text("{invalid json")
        monkeypatch.setenv("LLAMA_SWAP_CONFIG", str(bad_config))
        config = load_config()
        assert config == {}


class TestValidateConfig:
    def test_valid_config(self, set_config_env):
        config = load_config()
        warnings = validate_config(config)
        assert warnings == []

    def test_missing_services_dir(self):
        warnings = validate_config({"plists_dir": "/nonexistent/dir"})
        assert any("does not exist" in w for w in warnings)

    def test_invalid_health_url(self):
        warnings = validate_config({"health_url": "not-a-url"})
        assert any("health_url" in w for w in warnings)

    def test_invalid_health_timeout(self):
        warnings = validate_config({"health_timeout": "thirty"})
        assert any("health_timeout" in w for w in warnings)

    def test_negative_health_timeout(self):
        warnings = validate_config({"health_timeout": -5})
        assert any("health_timeout" in w for w in warnings)

    def test_missing_model_config(self, tmp_path):
        services_dir = tmp_path / "plists"
        services_dir.mkdir()
        config = {
            "plists_dir": str(services_dir),
            "models": {"ghost": "nonexistent.plist"},
        }
        warnings = validate_config(config)
        assert any("ghost" in w for w in warnings)


class TestGetHealthTimeout:
    def test_valid_timeout(self):
        assert get_health_timeout({"health_timeout": 60}) == 60

    def test_string_timeout(self):
        assert get_health_timeout({"health_timeout": "10"}) == 10

    def test_invalid_timeout_returns_default(self):
        assert get_health_timeout({"health_timeout": "bad"}) == 30

    def test_zero_timeout_returns_1(self):
        assert get_health_timeout({"health_timeout": 0}) == 1


class TestResolveServicesDir:
    def test_plists_dir_compat(self):
        config = {"plists_dir": "/foo/bar"}
        assert resolve_services_dir(config) == "/foo/bar"

    def test_services_dir_takes_precedence(self):
        config = {"services_dir": "/a", "plists_dir": "/b"}
        assert resolve_services_dir(config) == "/a"

    def test_units_dir_fallback(self):
        config = {"units_dir": "/u"}
        assert resolve_services_dir(config) == "/u"


# ---------------------------------------------------------------------------
# Service manager factory tests
# ---------------------------------------------------------------------------


class TestGetServiceManager:
    def test_auto_darwin(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        mgr = get_service_manager({})
        assert isinstance(mgr, LaunchctlManager)
        assert not mgr.modern

    def test_auto_darwin_modern(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        mgr = get_service_manager({"launchctl_mode": "modern"})
        assert isinstance(mgr, LaunchctlManager)
        assert mgr.modern

    def test_auto_linux(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        mgr = get_service_manager({})
        assert isinstance(mgr, SystemdManager)

    def test_explicit_launchctl(self):
        mgr = get_service_manager({"platform": "launchctl"})
        assert isinstance(mgr, LaunchctlManager)

    def test_explicit_systemd(self):
        mgr = get_service_manager({"platform": "systemd"})
        assert isinstance(mgr, SystemdManager)

    def test_unknown_platform(self):
        with pytest.raises(RuntimeError, match="Unknown platform"):
            get_service_manager({"platform": "windows"})

    def test_unsupported_auto(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        with pytest.raises(RuntimeError, match="Unsupported platform"):
            get_service_manager({})


# ---------------------------------------------------------------------------
# LaunchctlManager tests
# ---------------------------------------------------------------------------


class TestLaunchctlGetModels:
    def test_mapped_mode(self, set_config_env):
        config = load_config()
        mgr = LaunchctlManager()
        models = mgr.get_models(config)
        assert "coder" in models
        assert "planner" in models
        assert len(models) == 2

    def test_mapped_mode_skips_missing(self, set_config_env):
        config = load_config()
        config["models"]["ghost"] = "nonexistent.plist"
        mgr = LaunchctlManager()
        models = mgr.get_models(config)
        assert "ghost" not in models

    def test_directory_mode(self, set_config_env):
        config = load_config()
        config["models"] = {}
        mgr = LaunchctlManager()
        models = mgr.get_models(config)
        assert "coder" in models
        assert "planner" in models

    def test_empty_dir(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        config = {"plists_dir": str(empty_dir), "models": {}}
        mgr = LaunchctlManager()
        models = mgr.get_models(config)
        assert models == {}


class TestLaunchctlGetServiceLabel:
    def test_valid_plist(self, set_config_env):
        config = load_config()
        mgr = LaunchctlManager()
        models = mgr.get_models(config)
        label = mgr.get_service_label(models["coder"])
        assert label == "com.test.coder"

    def test_missing_plist(self):
        _get_plist_label_cached.cache_clear()
        mgr = LaunchctlManager()
        label = mgr.get_service_label("/nonexistent/path.plist")
        assert label is None

    def test_plist_without_label(self, tmp_path):
        _get_plist_label_cached.cache_clear()
        plist_path = tmp_path / "nolabel.plist"
        with open(plist_path, "wb") as f:
            plistlib.dump({"ProgramArguments": ["/bin/true"]}, f)
        mgr = LaunchctlManager()
        label = mgr.get_service_label(str(plist_path))
        assert label is None

    def test_corrupt_plist(self, tmp_path):
        _get_plist_label_cached.cache_clear()
        plist_path = tmp_path / "corrupt.plist"
        plist_path.write_text("this is not a valid plist")
        mgr = LaunchctlManager()
        label = mgr.get_service_label(str(plist_path))
        assert label is None

    def test_caching(self, set_config_env):
        _get_plist_label_cached.cache_clear()
        config = load_config()
        mgr = LaunchctlManager()
        models = mgr.get_models(config)
        path = models["coder"]

        label1 = mgr.get_service_label(path)
        label2 = mgr.get_service_label(path)
        assert label1 == label2
        assert _get_plist_label_cached.cache_info().hits >= 1


def _mock_subprocess(returncode=0, stdout=b"", stderr=b""):
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(stdout, stderr))
    mock_proc.returncode = returncode
    return mock_proc


class TestLaunchctlLegacyMode:
    @pytest.mark.asyncio
    async def test_is_loaded(self):
        mgr = LaunchctlManager(modern=False)
        mock_proc = _mock_subprocess(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            assert await mgr.is_loaded("com.test.label") is True

    @pytest.mark.asyncio
    async def test_load(self):
        mgr = LaunchctlManager(modern=False)
        mock_proc = _mock_subprocess(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            success, msg = await mgr.load("/path/to/test.plist")
        assert success is True

    @pytest.mark.asyncio
    async def test_unload(self):
        mgr = LaunchctlManager(modern=False)
        mock_proc = _mock_subprocess(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            success, msg = await mgr.unload("/path/to/test.plist", "com.test")
        assert success is True


class TestLaunchctlModernMode:
    @pytest.mark.asyncio
    async def test_is_loaded_uses_print(self):
        mgr = LaunchctlManager(modern=True)
        calls = []

        async def capture(*args, **kwargs):
            calls.append(args)
            return _mock_subprocess(returncode=0)

        with patch("asyncio.create_subprocess_exec", side_effect=capture):
            await mgr.is_loaded("com.test.label")

        # Should use "print gui/<uid>/com.test.label" instead of "list"
        assert any("print" in str(c) for c in calls)

    @pytest.mark.asyncio
    async def test_load_uses_bootstrap(self):
        mgr = LaunchctlManager(modern=True)
        calls = []

        async def capture(*args, **kwargs):
            calls.append(args)
            return _mock_subprocess(returncode=0)

        with patch("asyncio.create_subprocess_exec", side_effect=capture):
            await mgr.load("/path/to/test.plist")

        assert any("bootstrap" in str(c) for c in calls)

    @pytest.mark.asyncio
    async def test_unload_uses_bootout(self):
        mgr = LaunchctlManager(modern=True)
        calls = []

        async def capture(*args, **kwargs):
            calls.append(args)
            return _mock_subprocess(returncode=0)

        with patch("asyncio.create_subprocess_exec", side_effect=capture):
            await mgr.unload("/path/to/test.plist", "com.test.label")

        assert any("bootout" in str(c) for c in calls)


class TestLaunchctlCreateServiceConfig:
    def test_creates_plist(self, tmp_path):
        mgr = LaunchctlManager()
        path = mgr.create_service_config(
            name="test-model",
            llama_server_path="/usr/local/bin/llama-server",
            model_path="/models/test.gguf",
            services_dir=str(tmp_path),
            context_size=8192,
            gpu_layers=99,
            port=8001,
        )
        assert os.path.isfile(path)
        assert path.endswith(".plist")

        with open(path, "rb") as f:
            data = plistlib.load(f)
        assert data["Label"] == "com.llama-server.test-model"
        assert "/models/test.gguf" in data["ProgramArguments"]
        assert "8192" in data["ProgramArguments"]
        assert "99" in data["ProgramArguments"]
        assert "8001" in data["ProgramArguments"]


class TestLaunchctlGetLoadedModels:
    @pytest.mark.asyncio
    async def test_one_loaded(self, set_config_env):
        config = load_config()
        mgr = LaunchctlManager()
        models = mgr.get_models(config)

        async def fake_run(*args, **kwargs):
            cmd_args = args
            if "com.test.coder" in str(cmd_args):
                return _mock_subprocess(returncode=0)
            return _mock_subprocess(returncode=1)

        with patch("asyncio.create_subprocess_exec", side_effect=fake_run):
            loaded = await mgr.get_loaded_models(models)
        assert loaded == {"coder"}

    @pytest.mark.asyncio
    async def test_none_loaded(self, set_config_env):
        config = load_config()
        mgr = LaunchctlManager()
        models = mgr.get_models(config)

        mock_proc = _mock_subprocess(returncode=1)
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            loaded = await mgr.get_loaded_models(models)
        assert loaded == set()


class TestLaunchctlUnloadAll:
    @pytest.mark.asyncio
    async def test_unloads_loaded_models(self, set_config_env):
        config = load_config()
        mgr = LaunchctlManager()
        models = mgr.get_models(config)

        mock_proc = _mock_subprocess(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            unloaded, failed = await mgr.unload_all(models)

        assert set(unloaded) == {"coder", "planner"}
        assert failed == []

    @pytest.mark.asyncio
    async def test_reports_failed_unloads(self, set_config_env):
        config = load_config()
        mgr = LaunchctlManager()
        models = mgr.get_models(config)

        call_count = [0]

        async def alternate(*args, **kwargs):
            call_count[0] += 1
            # is_loaded returns True (rc=0), but unload fails (rc=1)
            cmd_args = args
            if "unload" in str(cmd_args):
                return _mock_subprocess(returncode=1, stderr=b"Operation not permitted")
            return _mock_subprocess(returncode=0)

        with patch("asyncio.create_subprocess_exec", side_effect=alternate):
            unloaded, failed = await mgr.unload_all(models)

        assert unloaded == []
        assert set(failed) == {"coder", "planner"}


class TestLaunchctlWaitForUnload:
    @pytest.mark.asyncio
    async def test_immediate_unload(self):
        mgr = LaunchctlManager()
        mock_proc = _mock_subprocess(returncode=1)
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await mgr.wait_for_unload("com.test.label", timeout=3)
        assert result is True

    @pytest.mark.asyncio
    async def test_timeout(self):
        mgr = LaunchctlManager()
        mock_proc = _mock_subprocess(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("mcp_llama_swap.launchctl.asyncio.sleep", new_callable=AsyncMock):
                result = await mgr.wait_for_unload("com.test.label", timeout=2)
        assert result is False


# ---------------------------------------------------------------------------
# SystemdManager tests
# ---------------------------------------------------------------------------


class TestSystemdGetModels:
    def test_mapped_mode(self, set_systemd_config_env):
        config = load_config()
        mgr = SystemdManager()
        models = mgr.get_models(config)
        assert "coder" in models
        assert "planner" in models
        assert len(models) == 2

    def test_directory_mode(self, set_systemd_config_env):
        config = load_config()
        config["models"] = {}
        mgr = SystemdManager()
        models = mgr.get_models(config)
        assert "llama-server-coder" in models
        assert "llama-server-planner" in models


class TestSystemdGetServiceLabel:
    def test_valid_unit(self, set_systemd_config_env):
        config = load_config()
        mgr = SystemdManager()
        models = mgr.get_models(config)
        label = mgr.get_service_label(models["coder"])
        assert label == "llama-server-coder.service"

    def test_missing_unit(self):
        mgr = SystemdManager()
        label = mgr.get_service_label("/nonexistent/path.service")
        assert label is None


class TestSystemdOperations:
    @pytest.mark.asyncio
    async def test_is_loaded(self):
        mgr = SystemdManager()
        mock_proc = _mock_subprocess(returncode=0, stdout=b"active\n")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            assert await mgr.is_loaded("llama-server-coder.service") is True

    @pytest.mark.asyncio
    async def test_is_not_loaded(self):
        mgr = SystemdManager()
        mock_proc = _mock_subprocess(returncode=3, stdout=b"inactive\n")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            assert await mgr.is_loaded("llama-server-coder.service") is False

    @pytest.mark.asyncio
    async def test_load(self, set_systemd_config_env):
        _, _, services_dir = set_systemd_config_env
        config = load_config()
        mgr = SystemdManager()
        models = mgr.get_models(config)

        mock_proc = _mock_subprocess(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            success, msg = await mgr.load(models["coder"])
        assert success is True

    @pytest.mark.asyncio
    async def test_unload(self):
        mgr = SystemdManager()
        mock_proc = _mock_subprocess(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            success, msg = await mgr.unload(
                "/path/to/unit.service", "llama-server-coder.service"
            )
        assert success is True


class TestSystemdCreateServiceConfig:
    def test_creates_unit_file(self, tmp_path):
        mgr = SystemdManager()
        path = mgr.create_service_config(
            name="test-model",
            llama_server_path="/usr/local/bin/llama-server",
            model_path="/models/test.gguf",
            services_dir=str(tmp_path),
            context_size=8192,
            gpu_layers=99,
            port=8001,
        )
        assert os.path.isfile(path)
        assert path.endswith(".service")

        content = open(path).read()
        assert "llama-server: test-model" in content
        assert "/models/test.gguf" in content
        assert "8192" in content
        assert "99" in content
        assert "8001" in content


class TestSystemdWaitForUnload:
    @pytest.mark.asyncio
    async def test_immediate_stop(self):
        mgr = SystemdManager()
        mock_proc = _mock_subprocess(returncode=3, stdout=b"inactive\n")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await mgr.wait_for_unload("llama-server-coder.service", timeout=3)
        assert result is True

    @pytest.mark.asyncio
    async def test_timeout(self):
        mgr = SystemdManager()
        mock_proc = _mock_subprocess(returncode=0, stdout=b"active\n")
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch(
                "mcp_llama_swap.systemd.asyncio.sleep", new_callable=AsyncMock
            ):
                result = await mgr.wait_for_unload(
                    "llama-server-coder.service", timeout=2
                )
        assert result is False


# ---------------------------------------------------------------------------
# Health check tests
# ---------------------------------------------------------------------------


class TestWaitForHealth:
    @pytest.mark.asyncio
    async def test_immediate_healthy(self):
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("mcp_llama_swap.service.httpx.AsyncClient", return_value=mock_client):
            result = await wait_for_health("http://localhost:9999/health", timeout=5)
        assert result is True

    @pytest.mark.asyncio
    async def test_timeout(self):
        import httpx

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("mcp_llama_swap.service.httpx.AsyncClient", return_value=mock_client):
            with patch(
                "mcp_llama_swap.service.asyncio.sleep", new_callable=AsyncMock
            ):
                result = await wait_for_health(
                    "http://localhost:9999/health", timeout=2
                )
        assert result is False


# ---------------------------------------------------------------------------
# MCP Tool tests (server-level integration)
# ---------------------------------------------------------------------------


class TestListModels:
    @pytest.mark.asyncio
    async def test_lists_models(self, set_config_env):
        mock_proc = _mock_subprocess(returncode=1)
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await server.list_models()
        assert "Mode: mapped" in result
        assert "coder" in result
        assert "planner" in result

    @pytest.mark.asyncio
    async def test_shows_loaded_status(self, set_config_env):
        async def fake_run(*args, **kwargs):
            if "com.test.coder" in str(args):
                return _mock_subprocess(returncode=0)
            return _mock_subprocess(returncode=1)

        with patch("asyncio.create_subprocess_exec", side_effect=fake_run):
            result = await server.list_models()
        assert "LOADED" in result


class TestGetCurrentModel:
    @pytest.mark.asyncio
    async def test_none_loaded(self, set_config_env):
        mock_proc = _mock_subprocess(returncode=1)
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await server.get_current_model()
        assert result == "No model currently loaded"

    @pytest.mark.asyncio
    async def test_one_loaded(self, set_config_env):
        async def fake_run(*args, **kwargs):
            if "com.test.planner" in str(args):
                return _mock_subprocess(returncode=0)
            return _mock_subprocess(returncode=1)

        with patch("asyncio.create_subprocess_exec", side_effect=fake_run):
            result = await server.get_current_model()
        assert "planner" in result


class TestSwapModel:
    @pytest.mark.asyncio
    async def test_model_not_found(self, set_config_env):
        result = await server.swap_model("nonexistent")
        assert "not found" in result
        assert "coder" in result

    @pytest.mark.asyncio
    async def test_successful_swap(self, set_config_env):
        from mcp_llama_swap.launchctl import LaunchctlManager

        async def fake_run(self_mgr, *args):
            # "list <label>" → not loaded; "load <path>" → success
            if args[0] == "list":
                return 1, "", "Could not find service"
            if args[0] == "load":
                return 0, "", ""
            return 1, "", ""

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(LaunchctlManager, "_run", fake_run),
            patch(
                "mcp_llama_swap.service.httpx.AsyncClient",
                return_value=mock_client,
            ),
        ):
            result = await server.swap_model("coder")
        assert "loaded and ready" in result

    @pytest.mark.asyncio
    async def test_load_failure(self, set_config_env):
        from mcp_llama_swap.launchctl import LaunchctlManager

        async def fake_run(self_mgr, *args):
            if args[0] == "load":
                return 1, "", "Operation not permitted"
            return 1, "", "Could not find service"

        with patch.object(LaunchctlManager, "_run", fake_run):
            result = await server.swap_model("coder")
        assert "Failed to load" in result
        assert "Operation not permitted" in result

    @pytest.mark.asyncio
    async def test_already_loaded_and_healthy(self, set_config_env):
        async def fake_run(*args, **kwargs):
            if "com.test.coder" in str(args):
                return _mock_subprocess(returncode=0)
            return _mock_subprocess(returncode=1)

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_run),
            patch(
                "mcp_llama_swap.service.httpx.AsyncClient",
                return_value=mock_client,
            ),
        ):
            result = await server.swap_model("coder")
        assert "already loaded and healthy" in result


class TestCreateModelConfig:
    @pytest.mark.asyncio
    async def test_non_absolute_path(self, set_config_env):
        result = await server.create_model_config(
            name="test", model_path="relative/path.gguf"
        )
        assert "absolute path" in result

    @pytest.mark.asyncio
    async def test_missing_model_file(self, set_config_env):
        result = await server.create_model_config(
            name="test", model_path="/nonexistent/model.gguf"
        )
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_missing_llama_server(self, set_config_env, tmp_path):
        model_file = tmp_path / "test.gguf"
        model_file.write_text("fake model")

        with patch("shutil.which", return_value=None):
            with patch("os.path.isfile", side_effect=lambda p: p == str(model_file)):
                result = await server.create_model_config(
                    name="test", model_path=str(model_file)
                )
        assert "Could not find llama-server" in result

    @pytest.mark.asyncio
    async def test_successful_creation(self, set_config_env, tmp_path):
        model_file = tmp_path / "test.gguf"
        model_file.write_text("fake model")

        with patch("shutil.which", return_value="/usr/local/bin/llama-server"):
            result = await server.create_model_config(
                name="test", model_path=str(model_file)
            )
        assert "Created service config" in result
        assert "test" in result
