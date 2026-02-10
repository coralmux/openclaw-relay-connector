#!/bin/bash
set -euo pipefail

# OpenClaw Agent Daemon - One-line installer
# Usage: curl -sSL https://relay.openclaw.dev/install | bash -s -- --token oc_pair_xxx

TOKEN=""
RELAY_URL="wss://relay.openclaw.dev/ws"

while [[ $# -gt 0 ]]; do
    case $1 in
        --token) TOKEN="$2"; shift 2;;
        --relay-url) RELAY_URL="$2"; shift 2;;
        *) echo "Unknown option: $1"; exit 1;;
    esac
done

echo "=== OpenClaw Agent Daemon Installer ==="

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "Error: Python 3 is required. Install it first."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [[ "$MAJOR" -lt 3 ]] || [[ "$MAJOR" -eq 3 && "$MINOR" -lt 10 ]]; then
    echo "Error: Python 3.10+ required, found $PYTHON_VERSION"
    exit 1
fi

echo "Python $PYTHON_VERSION OK"

# Install package
echo "Installing openclaw-agent..."
pip install --quiet openclaw-agent 2>/dev/null || pip3 install --quiet openclaw-agent

# Create config
CONFIG_DIR="$HOME/.openclaw-agent"
CONFIG_FILE="$CONFIG_DIR/config.yaml"
mkdir -p "$CONFIG_DIR"

if [[ ! -f "$CONFIG_FILE" ]]; then
    cat > "$CONFIG_FILE" << YAML
relay:
  url: ${RELAY_URL}
  token: "${TOKEN}"

backend:
  type: auto
  openai:
    api_key: \${OPENAI_API_KEY}
    model: gpt-4o
YAML
    echo "Config created: $CONFIG_FILE"
else
    echo "Config exists: $CONFIG_FILE (not overwritten)"
fi

# Install systemd service (Linux)
if [[ "$(uname)" == "Linux" ]] && command -v systemctl &>/dev/null; then
    AGENT_BIN=$(which openclaw-agent 2>/dev/null || echo "$HOME/.local/bin/openclaw-agent")

    sudo tee /etc/systemd/system/openclaw-agent.service > /dev/null << SERVICE
[Unit]
Description=OpenClaw Agent Daemon
After=network.target

[Service]
Type=simple
User=$USER
ExecStart=$AGENT_BIN run
Restart=always
RestartSec=5
Environment=HOME=$HOME

[Install]
WantedBy=multi-user.target
SERVICE

    sudo systemctl daemon-reload
    sudo systemctl enable openclaw-agent
    sudo systemctl start openclaw-agent
    echo "Systemd service installed and started"

# Install launchd plist (macOS)
elif [[ "$(uname)" == "Darwin" ]]; then
    AGENT_BIN=$(which openclaw-agent 2>/dev/null || echo "$HOME/.local/bin/openclaw-agent")
    PLIST_DIR="$HOME/Library/LaunchAgents"
    PLIST_FILE="$PLIST_DIR/dev.openclaw.agent.plist"
    mkdir -p "$PLIST_DIR"

    cat > "$PLIST_FILE" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>dev.openclaw.agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>$AGENT_BIN</string>
        <string>run</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$HOME/.openclaw-agent/agent.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/.openclaw-agent/agent.err</string>
</dict>
</plist>
PLIST

    launchctl load "$PLIST_FILE"
    echo "LaunchAgent installed and started"
fi

echo ""
echo "=== Installation complete ==="
echo "Config: $CONFIG_FILE"
echo "Logs:   openclaw-agent run -v"
echo ""
if [[ -z "$TOKEN" ]]; then
    echo "NOTE: Set your pairing token in $CONFIG_FILE"
fi
