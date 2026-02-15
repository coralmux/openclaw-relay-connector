"""CLI entrypoint for the OpenClaw Agent Daemon."""

import asyncio
import io
import logging
import signal
import sys
from pathlib import Path
from urllib.parse import urlencode

import click
import httpx
import qrcode
import yaml

from .agent_store import AgentStore
from .config import load_config, DEFAULT_CONFIG_PATH
from .backend import create_backend
from .backend.openclaw_gateway import OpenClawGatewayBackend
from .relay_client import RelayClient


def setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _relay_http_url(ws_url: str) -> str:
    """Convert ws(s):// URL to http(s):// base URL."""
    url = ws_url.replace("wss://", "https://").replace("ws://", "http://")
    # Strip /ws path
    if url.endswith("/ws"):
        url = url[:-3]
    return url


def _register_token(relay_url: str) -> str:
    """Register a new pairing token with the relay."""
    base = _relay_http_url(relay_url)
    resp = httpx.post(f"{base}/api/v1/register", timeout=10)
    resp.raise_for_status()
    return resp.json()["token"]


def _save_token_to_config(config_path: Path, token: str, relay_url: str):
    """Save token to config file, creating it if needed."""
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}

    data.setdefault("relay", {})
    data["relay"]["token"] = token
    data["relay"]["url"] = relay_url
    data.setdefault("backend", {"type": "auto", "openai": {"api_key": "${OPENAI_API_KEY}", "model": "gpt-4o"}})

    with open(config_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def _show_qr(token: str, relay_url: str):
    """Print QR code to terminal for phone pairing."""
    deep_link = f"openclaw://pair?token={token}&relay={relay_url}"

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=1)
    qr.add_data(deep_link)
    qr.make(fit=True)

    f = io.StringIO()
    qr.print_ascii(out=f, invert=True)
    qr_text = f.getvalue()

    click.echo()
    click.echo("=" * 50)
    click.echo("  Scan this QR code with your phone to pair:")
    click.echo("=" * 50)
    click.echo(qr_text)
    click.echo(f"  Token: {token}")
    click.echo(f"  Relay: {relay_url}")
    click.echo("=" * 50)
    click.echo()


@click.group()
def cli():
    """OpenClaw Agent Daemon - connects your AI backend to the relay."""
    pass


@cli.command()
@click.option("--token", "-t", help="Pairing token (overrides config)")
@click.option("--relay-url", "-r", help="Relay URL (overrides config)")
@click.option("--backend", "-b", help="Backend type: openclaw|openai|langgraph|autogen|crewai|auto")
@click.option("--config", "-c", type=click.Path(path_type=Path), help="Config file path")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def run(token, relay_url, backend, config, verbose):
    """Start the agent daemon."""
    setup_logging(verbose)
    logger = logging.getLogger("openclaw_agent")

    config_path = config or DEFAULT_CONFIG_PATH
    cfg = load_config(path=config_path if config_path.exists() else None,
                      token=token, relay_url=relay_url, backend_type=backend)

    # Auto-register if no token
    if not cfg.relay.token:
        click.echo("No pairing token found. Registering with relay...")
        try:
            new_token = _register_token(cfg.relay.url)
            cfg.relay.token = new_token
            _save_token_to_config(config_path, new_token, cfg.relay.url)
            click.echo(f"Token registered and saved to {config_path}")
        except Exception as e:
            click.echo(f"Error: Failed to register token: {e}")
            sys.exit(1)

    # Show QR code for pairing
    _show_qr(cfg.relay.token, cfg.relay.url)

    try:
        agent_backend = create_backend(cfg.backend)
    except ValueError as e:
        click.echo(f"Error: {e}")
        sys.exit(1)

    loop = asyncio.new_event_loop()

    # Connect to OpenClaw Gateway if using that backend
    if isinstance(agent_backend, OpenClawGatewayBackend):
        try:
            loop.run_until_complete(agent_backend.connect())
        except Exception as e:
            error_msg = str(e)
            click.echo(f"Error: Failed to connect to OpenClaw Gateway: {error_msg}")
            click.echo()
            if "device identity required" in error_msg.lower():
                click.echo("OpenClaw Gateway requires device identity authentication.")
                click.echo("To allow token-based auth, add this to your OpenClaw config.yaml:")
                click.echo()
                click.echo("  gateway:")
                click.echo("    controlUi:")
                click.echo("      allowInsecureAuth: true")
                click.echo()
                click.echo("Then restart OpenClaw and try again.")
            elif "connection refused" in error_msg.lower():
                click.echo("Make sure OpenClaw Gateway is running:")
                click.echo("  openclaw")
                click.echo()
                click.echo(f"Expected Gateway at: {cfg.backend.openclaw.gateway_url}")
            sys.exit(1)
        agent_store = None  # OpenClaw manages agents via workspace config
        logger.info("Using OpenClaw Gateway — agents managed via workspace config")
    else:
        # Initialize agent store and ensure at least one default agent exists
        agent_store = AgentStore()
        default_agent = agent_store.ensure_default()
        logger.info(f"Agent store ready: {len(agent_store.list())} agent(s), default: {default_agent.name}")

    client = RelayClient(cfg.relay.url, cfg.relay.token, agent_backend,
                         agent_store=agent_store, api_base_url=cfg.api.base_url)

    logger.info(f"Starting agent daemon (backend: {cfg.backend.type})")
    logger.info(f"Relay: {cfg.relay.url}")

    def shutdown(sig, frame):
        logger.info("Shutting down...")
        client.stop()
        if isinstance(agent_backend, OpenClawGatewayBackend):
            loop.run_until_complete(agent_backend.disconnect())
        loop.call_soon_threadsafe(loop.stop)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        loop.run_until_complete(client.run())
    except KeyboardInterrupt:
        pass
    finally:
        if isinstance(agent_backend, OpenClawGatewayBackend):
            loop.run_until_complete(agent_backend.disconnect())
        loop.close()


@cli.command()
@click.option("--config", "-c", type=click.Path(path_type=Path), help="Config file path")
def init(config):
    """Initialize config file."""
    config_path = config or DEFAULT_CONFIG_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        click.echo(f"Config already exists: {config_path}")
        return

    template = """\
relay:
  url: wss://relay.coralmux.com/ws
  token: ""  # Auto-filled on first run

backend:
  type: auto  # auto | openclaw | openai | langgraph | autogen | crewai
  openai:
    api_key: ${OPENAI_API_KEY}
    model: gpt-4o
  # openclaw:
  #   gateway_url: ws://127.0.0.1:18789
  #   token: ${OPENCLAW_GATEWAY_TOKEN}
  # langgraph:
  #   base_url: http://localhost:8000
  #   runnable: my-agent
"""
    config_path.write_text(template)
    click.echo(f"Config created: {config_path}")
    click.echo("Edit the file to configure your backend, then run: openclaw-agent run")


if __name__ == "__main__":
    cli()
