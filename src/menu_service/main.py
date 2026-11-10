"""Entrypoint of the Menu service: `uv run menu-service` (also started by `uv run delivery-agent`)."""

import logging

import uvicorn

from menu_service.app import create_app
from menu_service.config import get_settings

LOG_FORMAT = "ts=%(asctime)s level=%(levelname)s logger=%(name)s service=menu msg=%(message)s"
NOISY_LOGGERS = ("httpx", "httpx2", "httpcore", "httpcore2", "openai", "lancedb", "urllib3", "filelock", "asyncio")


class WindowsSocketResetFilter(logging.Filter):
    """Drops asyncio's harmless "connection reset" traceback on Windows.

    When a peer has already reset a keep-alive socket, the Proactor event loop's `_call_connection_lost` calls
    `socket.shutdown()` without catching the error and logs `ConnectionResetError` (WinError 10054). The connection was
    being discarded anyway and no request fails, so the traceback is noise.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Return False only for that specific asyncio callback error."""
        exc = record.exc_info[1] if record.exc_info else None
        return not (isinstance(exc, ConnectionResetError) and "_call_connection_lost" in str(record.msg))


def configure_logging(level: str) -> None:
    """Log in the same key=value style as the agent, keeping third-party libraries at WARNING."""
    logging.basicConfig(level=level.upper(), format=LOG_FORMAT, force=True)
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    asyncio_logger = logging.getLogger("asyncio")
    if not any(isinstance(f, WindowsSocketResetFilter) for f in asyncio_logger.filters):
        asyncio_logger.addFilter(WindowsSocketResetFilter())


def run() -> None:
    """Serve the Menu service API with uvicorn."""
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(settings),
        host=settings.menu_service_host,
        port=settings.menu_service_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
