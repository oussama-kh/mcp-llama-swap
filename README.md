# mcp-llama-swap

[![PyPI version](https://img.shields.io/pypi/v/mcp-llama-swap.svg)](https://pypi.org/project/mcp-llama-swap/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**Hot-swap llama.cpp models inside a running Claude Code session on macOS. No context loss. One command.**

> Plan with a reasoning model. Implement with a coding model. Same session, same context, zero manual overhead.

<!-- TODO: Replace with actual recording
![demo](https://github.com/oussama-kh/mcp-llama-swap/raw/main/demo.gif)
-->

## Why

Running local LLMs on Apple Silicon means choosing between a strong reasoning model and a fast coding model. You can't load both on a single machine. Manually swapping models kills your conversation context and flow.

mcp-llama-swap solves this by giving Claude Code a tool to swap the model behind llama-server via launchctl, while preserving the full conversation history client-side.

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

Create `config.json`:

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

### Use

Inside Claude Code:

```
You: list models
You: swap to planner
You: <discuss architecture, define interfaces>
You: swap to coder and implement the plan
```

That's it. Context is preserved across swaps.

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
llama-server (:8000)          <-- model weights swapped via launchctl
    ^
    |
mcp-llama-swap                <-- this project
```

Claude Code speaks Anthropic format. LiteLLM translates to OpenAI format for llama-server. This MCP server manages which model plist is loaded via launchctl.

Conversation context survives swaps because Claude Code holds the full message history client-side and re-sends it with every request.

## Model Configuration

### Mapped Mode (recommended)

Define aliases for your models. Only mapped models are available. Other plists in the directory are ignored.

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

Swap using your aliases: "swap to coder", "swap to planner".

### Directory Mode

Set `"models": {}` to auto-discover all `.plist` files. Filenames (without `.plist`) become the aliases.

```json
{
  "plists_dir": "~/.llama-plists",
  "models": {}
}
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `list_models` | Lists all configured models with load status and current mode |
| `get_current_model` | Returns the alias of the currently loaded model |
| `swap_model` | Unloads current model, loads the specified one, waits for health check |

## Full Setup Guide

### Prerequisites

- macOS with launchctl
- llama-server (llama.cpp) installed
- Model configurations as launchd plist files in a directory
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

Or use the included `ai.litellm.proxy.plist` to run it as a persistent launchd service (edit paths first, then `cp` to `~/Library/LaunchAgents/` and `launchctl load`).

### 3. Point Claude Code at LiteLLM

Add to `~/.zshrc`:

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

Copy `config.example.json` and edit with your model aliases and plist filenames.

### Automated Setup

If you prefer a one-shot setup, clone this repo and run:

```bash
git clone https://github.com/oussama-kh/mcp-llama-swap.git ~/mcp-llama-swap
cd ~/mcp-llama-swap
chmod +x setup.sh
./setup.sh
```

The script installs dependencies, configures the LiteLLM launchd service, and prints the exact config to add.

## Configuration Reference

`config.json` fields:

| Field | Default | Description |
|-------|---------|-------------|
| `plists_dir` | `~/.llama-plists` | Directory containing model plist files |
| `health_url` | `http://localhost:8000/health` | llama-server health endpoint |
| `health_timeout` | `30` | Seconds to wait for health check after loading |
| `models` | `{}` | Alias-to-filename map. Empty = directory mode |

Override config path via the `LLAMA_SWAP_CONFIG` environment variable.

## Troubleshooting

**LiteLLM not translating correctly:** Check `/tmp/litellm.stderr.log`. Verify llama-server is running: `curl http://localhost:8000/health`.

**Model swap times out:** Increase `health_timeout` in `config.json`. Large models may need 30+ seconds to load weights into memory.

**Claude Code cannot find the MCP server:** Verify the `LLAMA_SWAP_CONFIG` path is absolute. Test directly: `python -m mcp_llama_swap`.

**Mapped model not found:** The plist filename in `models` must match an actual file in `plists_dir`.

## Use Case

This project enables a two-phase AI coding workflow entirely on local hardware:

1. **Planning phase:** Load a reasoning model (e.g., Qwen3.5-35B-A3B with thinking). Discuss architecture, define interfaces, decompose requirements.
2. **Implementation phase:** Swap to a coding model (e.g., Qwen3-Coder-30B). Execute the plan file by file with full conversation context from the planning phase.

No cloud APIs. No data leaving your machine. No context loss between phases.

## License

Apache-2.0
