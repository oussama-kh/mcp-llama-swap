# mcp-llama-swap

[![PyPI version](https://img.shields.io/pypi/v/mcp-llama-swap.svg)](https://pypi.org/project/mcp-llama-swap/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**Hot-swap llama.cpp models inside a running Claude Code session. No context loss. One command.**

> Plan with a reasoning model. Implement with a coding model. Same session, same context, zero manual overhead.

Supports **macOS** (launchctl) and **Linux** (systemd).

<!-- TODO: Replace with actual recording
![demo](https://github.com/oussama-kh/mcp-llama-swap/raw/main/demo.gif)
-->

## Why

Running local LLMs means choosing between a strong reasoning model and a fast coding model. You can't load both on a single machine. Manually swapping models kills your conversation context and flow.

mcp-llama-swap solves this by giving Claude Code a tool to swap the model behind llama-server via your system's service manager (launchctl on macOS, systemd on Linux), while preserving the full conversation history client-side.

## Quick Start

### Install

```bash
# Option A: Run directly with uvx (no install needed)
uvx mcp-llama-swap

# Option B: Install from PyPI
pip install mcp-llama-swap
```

### Configure Claude Code

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "llama-swap": {
      "command": "uvx",
      "args": ["mcp-llama-swap"],
      "env": {
        "LLAMA_SWAP_CONFIG": "/path/to/config.json"
      }
    }
  }
}
```

### Configure Models

Create `config.json` (macOS):

```json
{
  "plists_dir": "~/.llama-plists",
  "health_url": "http://localhost:8000/health",
  "health_timeout": 30,
  "models": {
    "planner": "qwen35-thinking.plist",
    "coder": "qwen3-coder.plist",
    "fast": "glm-flash.plist"
  }
}
```

Or on Linux:

```json
{
  "services_dir": "~/.llama-services",
  "health_url": "http://localhost:8000/health",
  "health_timeout": 30,
  "models": {
    "planner": "llama-server-planner.service",
    "coder": "llama-server-coder.service"
  }
}
```

### Use

Inside Claude Code:

```
You: list models
You: swap to planner
You: <discuss architecture, define interfaces>
You: swap to coder and implement the plan
```

That's it. Context is preserved across swaps.

You can also generate new model configs directly:

```
You: create a model config named "reasoning" for /models/qwen3-30b.gguf with 8192 context
```

## How It Works

```
Claude Code CLI
    |
    | Anthropic Messages API
    v
LiteLLM Proxy (:4000)         <-- translates Anthropic -> OpenAI format
    |
    | OpenAI Chat Completions API
    v
llama-server (:8000)          <-- model weights swapped via service manager
    ^
    |
mcp-llama-swap                <-- this project (launchctl or systemd)
```

Claude Code speaks Anthropic format. LiteLLM translates to OpenAI format for llama-server. This MCP server manages which model service is loaded via launchctl (macOS) or systemd (Linux).

Conversation context survives swaps because Claude Code holds the full message history client-side and re-sends it with every request.

## Model Configuration

### Mapped Mode (recommended)

Define aliases for your models. Only mapped models are available. Other service configs in the directory are ignored.

macOS:

```json
{
  "plists_dir": "~/.llama-plists",
  "health_url": "http://localhost:8000/health",
  "health_timeout": 30,
  "models": {
    "planner": "qwen35-35b-a3b-thinking.plist",
    "coder": "qwen3-coder.plist",
    "fast": "glm-4-7-flash.plist"
  }
}
```

Linux:

```json
{
  "services_dir": "~/.llama-services",
  "health_url": "http://localhost:8000/health",
  "health_timeout": 30,
  "models": {
    "planner": "llama-server-planner.service",
    "coder": "llama-server-coder.service"
  }
}
```

Swap using your aliases: "swap to coder", "swap to planner".

### Directory Mode

Set `"models": {}` to auto-discover all service configs. Filenames (without extension) become the aliases.

macOS:

```json
{
  "plists_dir": "~/.llama-plists",
  "models": {}
}
```

Linux:

```json
{
  "services_dir": "~/.llama-services",
  "models": {}
}
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `list_models` | Lists all configured models with load status and current mode |
| `get_current_model` | Returns the alias of the currently loaded model |
| `swap_model` | Unloads current model, loads the specified one, waits for health check |
| `create_model_config` | Generates a new launchd plist (macOS) or systemd unit (Linux) for a model |

## MCP Resources

| Resource | Description |
|----------|-------------|
| `llama-swap://config` | Current configuration as JSON |
| `llama-swap://status` | Current model status, health, and platform info |

## MCP Prompts

| Prompt | Description |
|--------|-------------|
| `swap-workflow` | Guided plan-then-implement workflow template |

## Full Setup Guide

### Prerequisites

- **macOS** with launchctl, or **Linux** with systemd
- llama-server (llama.cpp) installed
- Model configurations as service files (launchd plists or systemd units)
- Python 3.10+
- Claude Code CLI pointed at a LiteLLM proxy

### 1. Install mcp-llama-swap

```bash
pip install mcp-llama-swap
```

### 2. Install and start LiteLLM proxy

```bash
pip install litellm
```

Create `litellm_config.yaml`:

```yaml
model_list:
  - model_name: "*"
    litellm_params:
      model: "openai/*"
      api_base: "http://localhost:8000/v1"
      api_key: "sk-none"

litellm_settings:
  drop_params: true
  request_timeout: 300
```

Start it:

```bash
litellm --config litellm_config.yaml --port 4000
```

On macOS, you can use the included `ai.litellm.proxy.plist.template` to run it as a persistent launchd service (see `setup.sh`).

### 3. Point Claude Code at LiteLLM

Add to `~/.zshrc` (macOS) or `~/.bashrc` (Linux):

```bash
export ANTHROPIC_BASE_URL="http://localhost:4000"
export ANTHROPIC_API_KEY="sk-none"
export ANTHROPIC_MODEL="local"
```

### 4. Add MCP server to Claude Code

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "llama-swap": {
      "command": "uvx",
      "args": ["mcp-llama-swap"],
      "env": {
        "LLAMA_SWAP_CONFIG": "/absolute/path/to/config.json"
      }
    }
  }
}
```

### 5. Create your config.json

Copy `config.example.json` (macOS) or `config.example.linux.json` (Linux) and edit with your model aliases and service filenames.

### 6. Create model service configs

You can create service configs manually, or use the `create_model_config` MCP tool inside Claude Code:

```
You: create a model config named "coder" for /path/to/model.gguf with 8192 context
```

This generates the appropriate launchd plist (macOS) or systemd unit file (Linux) in your services directory.

### Automated Setup (macOS)

If you prefer a one-shot setup on macOS, clone this repo and run:

```bash
git clone https://github.com/oussama-kh/mcp-llama-swap.git ~/mcp-llama-swap
cd ~/mcp-llama-swap
chmod +x setup.sh
./setup.sh
```

The script creates a virtual environment, installs dependencies, configures the LiteLLM launchd service, and prints the exact config to add.

## Configuration Reference

`config.json` fields:

| Field | Default | Description |
|-------|---------|-------------|
| `services_dir` | `~/.llama-plists` (macOS) / `~/.llama-services` (Linux) | Directory containing model service configs |
| `plists_dir` | — | macOS alias for `services_dir` (backwards compatible) |
| `units_dir` | — | Linux alias for `services_dir` |
| `health_url` | `http://localhost:8000/health` | llama-server health endpoint |
| `health_timeout` | `30` | Seconds to wait for health check after loading |
| `models` | `{}` | Alias-to-filename map. Empty = directory mode |
| `platform` | `auto` | Service manager: `auto`, `launchctl`, or `systemd` |
| `launchctl_mode` | `legacy` | macOS only: `legacy` (load/unload) or `modern` (bootstrap/bootout) |

Override config path via the `LLAMA_SWAP_CONFIG` environment variable.

## Platform Details

### macOS (launchctl)

Models are managed as launchd services via plist files. Two launchctl modes are available:

- **Legacy** (default): Uses `launchctl load/unload/list`. Works on all macOS versions.
- **Modern**: Uses `launchctl bootstrap/bootout/print`. The officially supported API on newer macOS. Enable with `"launchctl_mode": "modern"` in config.

### Linux (systemd)

Models are managed as systemd user services. Unit files in `services_dir` are symlinked to `~/.config/systemd/user/` and managed via `systemctl --user start/stop`.

## Troubleshooting

**LiteLLM not translating correctly:** Check `/tmp/litellm.stderr.log`. Verify llama-server is running: `curl http://localhost:8000/health`.

**Model swap times out:** Increase `health_timeout` in `config.json`. Large models may need 30+ seconds to load weights into memory.

**Claude Code cannot find the MCP server:** Verify the `LLAMA_SWAP_CONFIG` path is absolute. Test directly: `python -m mcp_llama_swap`.

**Mapped model not found:** The service filename in `models` must match an actual file in your services directory.

**systemd service won't start:** Check `journalctl --user -u llama-server-<name>` for errors. Ensure `llama-server` is in your PATH.

**launchctl modern mode issues:** If `bootstrap`/`bootout` commands fail, fall back to `"launchctl_mode": "legacy"` in config.

## Development

```bash
# Install with test dependencies
pip install -e ".[test]"

# Run tests
pytest -v
```

## Use Case

This project enables a two-phase AI coding workflow entirely on local hardware:

1. **Planning phase:** Load a reasoning model (e.g., Qwen3.5-35B-A3B with thinking). Discuss architecture, define interfaces, decompose requirements.
2. **Implementation phase:** Swap to a coding model (e.g., Qwen3-Coder-30B). Execute the plan file by file with full conversation context from the planning phase.

No cloud APIs. No data leaving your machine. No context loss between phases.

## License

Apache-2.0
