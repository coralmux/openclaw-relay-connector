# OpenClaw Relay Connector

Python daemon that connects [OpenClaw](https://github.com/openclaw/openclaw) Gateway to [CoralMux Relay](https://github.com/coralmux/relay). Run it on your home server, laptop, or any machine with internet access — no port forwarding needed.

## How It Works

```
Your Server                         CoralMux Relay                 Your Phone
┌────────────────────┐          ┌─────────────────┐          ┌──────────────┐
│ openclaw-relay-    │──WSS──→ │ relay.coralmux  │ ←──WSS──│ Mobile App   │
│ connector          │ outbound│ .com            │ outbound│              │
│                    │         │                  │         │              │
│ ┌────────────────┐ │         │  - routing      │         └──────────────┘
│ │ OpenClaw       │ │         │  - E2E encrypt  │
│ │ Gateway        │ │         │  - rate limit   │
│ │ (localhost)    │ │         └─────────────────┘
│ └────────────────┘ │
└────────────────────┘
```

Both sides make **outbound** WebSocket connections. Works behind any NAT/firewall.

## Quick Start

### Install

```bash
pip install openclaw-relay-connector
```

Or from source:

```bash
git clone https://github.com/coralmux/openclaw-relay-connector.git
cd openclaw-relay-connector
pip install -e .
```

### Configure

```bash
openclaw-relay-connector init
# Creates ~/.openclaw-relay-connector/config.yaml
```

Edit `~/.openclaw-relay-connector/config.yaml`:

```yaml
relay:
  url: wss://relay.coralmux.com/ws
  token: oc_pair_YOUR_TOKEN_HERE

gateway:
  url: ws://localhost:18789   # OpenClaw Gateway WebSocket
```

### Run

```bash
openclaw-relay-connector run
```

Or as a system service:

```bash
# Linux (systemd)
sudo cp systemd/openclaw-relay-connector.service /etc/systemd/system/
sudo systemctl enable --now openclaw-relay-connector
```

## E2E Encryption

The connector performs X25519 key exchange with the phone app on each connection. All message payloads are encrypted with AES-256-GCM. **The relay server cannot read your conversations.**

```
Connector                          Relay                         Phone
  │── auth(token) ────────────────→│                              │
  │←─ auth.ok ────────────────────│                              │
  │── key_exchange(pubkey) ───────→│── forward ──────────────────→│
  │←─ key_exchange(pubkey) ────────│←─ forward ───────────────────│
  │                                │                              │
  │  shared key derived (HKDF)     │     (cannot decrypt)         │  shared key derived
  │                                │                              │
  │←─ chat.send{encrypted} ────────│←─ forward ───────────────────│
  │   decrypt → Gateway → encrypt  │                              │
  │── chat.stream{encrypted} ─────→│── forward ──────────────────→│  decrypt → display
```

## Connection Behavior

- Auto-reconnects on disconnection (exponential backoff: 5s → 60s max)
- Re-establishes E2E encryption on each reconnection
- Regenerates keypair when peer reconnects
- Graceful shutdown on SIGINT/SIGTERM

## Project Structure

```
src/openclaw_relay_connector/
  main.py              # CLI entrypoint
  config.py            # YAML config loading
  relay_client.py      # WebSocket connection, auth, E2E, message loop
  protocol.py          # Protocol message types
  e2e_crypto.py        # X25519 + AES-256-GCM
  backend/
    openclaw_gateway.py  # OpenClaw Gateway WebSocket client
```

## Requirements

- Python 3.10+
- OpenClaw Gateway running locally
- Pairing token from CoralMux Relay

## Related Projects

- [CoralMux Relay](https://github.com/coralmux/relay) — NAT-transparent relay server
- [OpenClaw](https://github.com/openclaw/openclaw) — AI agent gateway

## License

MIT
