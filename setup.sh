#!/bin/bash
set -e

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="ai.litellm.proxy.plist"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

echo "=== mcp-llama-swap setup ==="
echo "Install directory: $INSTALL_DIR"
echo ""

# 1. Install Python dependencies
echo "[1/4] Installing Python dependencies..."
pip3 install --break-system-packages mcp-llama-swap litellm
echo ""

# 2. Resolve litellm binary path and patch plist
echo "[2/4] Configuring LiteLLM plist..."
LITELLM_BIN=$(which litellm)
if [ -z "$LITELLM_BIN" ]; then
    echo "ERROR: litellm binary not found in PATH"
    exit 1
fi
echo "  Found litellm at: $LITELLM_BIN"

CONFIG_YAML="$INSTALL_DIR/litellm_config.yaml"
sed -i '' "s|__LITELLM_BIN__|${LITELLM_BIN}|" "$INSTALL_DIR/$PLIST_NAME"
sed -i '' "s|__CONFIG_PATH__|${CONFIG_YAML}|" "$INSTALL_DIR/$PLIST_NAME"
echo ""

# 3. Install and load LiteLLM plist
echo "[3/4] Installing LiteLLM launchd service..."
mkdir -p "$LAUNCH_AGENTS_DIR"
cp "$INSTALL_DIR/$PLIST_NAME" "$LAUNCH_AGENTS_DIR/$PLIST_NAME"
launchctl load "$LAUNCH_AGENTS_DIR/$PLIST_NAME"

echo "  Waiting for LiteLLM proxy on port 4000..."
for i in $(seq 1 15); do
    if curl -s http://localhost:4000/health > /dev/null 2>&1; then
        echo "  LiteLLM proxy is running."
        break
    fi
    sleep 1
done
echo ""

# 4. Print Claude Code config
CONFIG_JSON="$INSTALL_DIR/config.json"

echo "[4/4] Configuration instructions"
echo ""
echo "--- Add to ~/.claude.json (merge into existing mcpServers if present) ---"
echo ""
cat <<EOF
{
  "mcpServers": {
    "llama-swap": {
      "command": "uvx",
      "args": ["mcp-llama-swap"],
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
echo "--- GitHub topics to set on your repo ---"
echo ""
echo "mcp, llama-cpp, claude-code, model-context-protocol, macos,"
echo "launchctl, local-llm, apple-silicon, model-switching"
echo ""
echo "=== Setup complete ==="
