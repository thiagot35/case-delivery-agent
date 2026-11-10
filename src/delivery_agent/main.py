"""Single-command entrypoint: `uv run delivery-agent`.

Starts the Menu service as a child process (unless it is already running or autostart is off), waits until it is
healthy, then serves the agent's HTTP API. The child is stopped when the agent exits.
"""

import logging
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import uvicorn

from delivery_agent.api.app import create_app
from delivery_agent.config import Settings, get_settings
from delivery_agent.logging_config import configure_logging

logger = logging.getLogger(__name__)

HEALTH_POLL_SECONDS = 0.5


def menu_service_healthy(base_url: str) -> bool:
    """Tell whether the Menu service answers its health check."""
    try:
        return httpx.get(f"{base_url.rstrip('/')}/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


@contextmanager
def menu_service_process(settings: Settings) -> Iterator[subprocess.Popen[bytes] | None]:
    """Run the Menu service as a child process for the duration of the block (when autostart applies)."""
    if not settings.menu_service_autostart:
        yield None
        return
    if menu_service_healthy(settings.menu_service_base_url):
        logger.info("Menu service already running at %s", settings.menu_service_base_url)
        yield None
        return

    logger.info("Starting the Menu service (%s)", settings.menu_service_base_url)
    process = subprocess.Popen([sys.executable, "-m", "menu_service.main"])
    try:
        deadline = time.monotonic() + settings.menu_service_startup_timeout_seconds
        while not menu_service_healthy(settings.menu_service_base_url):
            if process.poll() is not None:
                raise RuntimeError(f"Menu service exited during startup (code {process.returncode})")
            if time.monotonic() > deadline:
                raise RuntimeError("Menu service did not become healthy in time")
            time.sleep(HEALTH_POLL_SECONDS)
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


def run() -> None:
    """Entry point of `uv run delivery-agent`: start the Menu service, then serve the agent API with uvicorn."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_color)
    with menu_service_process(settings):
        uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    run()
