#!/bin/bash
set -e

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_TEMPLATE="$INSTALL_DIR/ai.litellm.proxy.plist.template"
PLIST_NAME="ai.litellm.proxy.plist"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
VENV_DIR="$INSTALL_DIR/.venv"

echo "=== mcp-llama-swap setup ==="
echo "Install directory: $INSTALL_DIR"
echo ""

# 1. Install Python dependencies in a virtual environment
echo "[1/4] Installing Python dependencies..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "  Created virtual environment at $VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --quiet mcp-llama-swap litellm
echo "  Installed mcp-llama-swap and litellm into $VENV_DIR"
echo ""

# 2. Resolve litellm binary path and generate plist
echo "[2/4] Configuring LiteLLM plist..."
LITELLM_BIN="$VENV_DIR/bin/litellm"
if [ ! -f "$LITELLM_BIN" ]; then
    # Fall back to system-wide litellm
    LITELLM_BIN=$(which litellm 2>/dev/null || true)
    if [ -z "$LITELLM_BIN" ]; then
        echo "ERROR: litellm binary not found"
        exit 1
    fi
fi
echo "  Found litellm at: $LITELLM_BIN"

CONFIG_YAML="$INSTALL_DIR/litellm_config.yaml"

# Generate plist from template — never modify source files
if [ ! -f "$PLIST_TEMPLATE" ]; then
    echo "ERROR: plist template not found at $PLIST_TEMPLATE"
    exit 1
fi
GENERATED_PLIST="$LAUNCH_AGENTS_DIR/$PLIST_NAME"
mkdir -p "$LAUNCH_AGENTS_DIR"
sed -e "s|__LITELLM_BIN__|${LITELLM_BIN}|g" \
    -e "s|__CONFIG_PATH__|${CONFIG_YAML}|g" \
    "$PLIST_TEMPLATE" > "$GENERATED_PLIST"
echo "  Generated plist at: $GENERATED_PLIST"
echo ""

# 3. Load LiteLLM plist
echo "[3/4] Installing LiteLLM launchd service..."

# Unload existing service if present (ignore errors)
launchctl unload "$GENERATED_PLIST" 2>/dev/null || true

launchctl load "$GENERATED_PLIST"

echo "  Waiting for LiteLLM proxy on port 4000..."
for i in $(seq 1 15); do
    if curl -s http://localhost:4000/health > /dev/null 2>&1; then
        echo "  LiteLLM proxy is running."
        break
    fi
    if [ "$i" -eq 15 ]; then
        echo "  Warning: LiteLLM proxy not responding after 15s. Check /tmp/litellm.stderr.log"
    fi
    sleep 1
done
echo ""

# 4. Print Claude Code config
CONFIG_JSON="$INSTALL_DIR/config.json"
MCP_LLAMA_SWAP_BIN="$VENV_DIR/bin/mcp-llama-swap"

echo "[4/4] Configuration instructions"
echo ""
echo "--- Add to ~/.claude.json (merge into existing mcpServers if present) ---"
echo ""
cat <<EOF
{
  "mcpServers": {
    "llama-swap": {
      "command": "$MCP_LLAMA_SWAP_BIN",
      "args": [],
      "env": {
        "LLAMA_SWAP_CONFIG": "$CONFIG_JSON"
      }
    }
  }
}
EOF
echo ""
echo "--- Add to ~/.zshrc ---"
echo ""
echo "export ANTHROPIC_BASE_URL=\"http://localhost:4000\""
echo "export ANTHROPIC_API_KEY=\"sk-none\""
echo "export ANTHROPIC_MODEL=\"local\""
echo ""
echo "--- Model configuration ---"
echo ""
echo "Edit $CONFIG_JSON to choose between:"
echo "  Directory mode: set \"models\": {} to auto-discover all plists"
echo "  Mapped mode:    set \"models\": {\"alias\": \"file.plist\", ...}"
echo "  See config.example.json for a mapped mode example."
echo ""
echo "=== Setup complete ==="
