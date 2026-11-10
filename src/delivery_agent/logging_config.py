"""Logging setup: key=value lines on a single root handler.

Every record that reaches the root handler (this project's loggers and Agno's, which are re-routed here) gets:
- `session=`: the conversation being processed, bound by `ConversationService` through `log_context()`, so the
  lines of concurrent conversations can be told apart;
- CPF masking: anything shaped like a CPF is replaced by `***.***.*NN-NN`, so DEBUG output (prompts, payloads,
  tool arguments) never leaks customer documents.

`LOG_LEVEL=DEBUG` also enables Agno's debug output (prompts, model calls, tool calls, metrics): see
`agent/factory.py`.

On a terminal the lines are colored (`LOG_COLOR=auto`): the level name by severity, whole WARNING/ERROR lines,
dimmed DEBUG lines, a bold cyan line for each incoming request (`extra=REQUEST_HIGHLIGHT`) and an orange line when a
pipeline step starts (`extra=STEP_HIGHLIGHT`). Piped or redirected output stays plain, and `NO_COLOR` is honored.
"""

import logging
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal, TextIO

from delivery_agent.domain.cpf import mask_cpf

LOG_FORMAT = "ts=%(asctime)s level=%(levelname)s logger=%(name)s session=%(session)s msg=%(message)s"

# Agno's loggers ship with their own handler and `propagate=False`. They are re-routed to the root handler so all
# output shares one format, the session field and the CPF masking.
AGNO_LOGGERS = ("agno", "agno-team", "agno-workflow")

# Third-party loggers that are either very verbose at DEBUG or log URLs/rows without masking.
NOISY_LOGGERS = (
    "httpx",
    "httpx2",
    "httpcore",
    "httpcore2",
    "openai",
    "sqlalchemy",
    "urllib3",
    "lancedb",
    "filelock",
    "asyncio",
    "multipart",
    "python_multipart",
)

# 11 digits, with or without the `000.000.000-00` punctuation, not glued to other letters/digits.
_CPF_PATTERN = re.compile(r"(?<!\w)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\w)")

_session_var: ContextVar[str] = ContextVar("log_session", default="-")

ColorMode = Literal["auto", "always", "never"]

# ANSI escape codes (rendered by Windows Terminal, the VS Code terminal and any Unix terminal).
RESET = "\033[0m"
HIGHLIGHT = "\033[1;96m"  # bold bright cyan: incoming requests
STEP_COLOR = "\033[38;5;208m"  # orange (256-color palette): pipeline steps
LEVEL_COLORS = {
    logging.DEBUG: "\033[2m",  # dim
    logging.INFO: "\033[32m",  # green
    logging.WARNING: "\033[33m",  # yellow
    logging.ERROR: "\033[31m",  # red
    logging.CRITICAL: "\033[1;31m",  # bold red
}
# Levels whose whole line is colored (INFO colors only its level name, so the message stays easy to read).
WHOLE_LINE_LEVELS = (logging.DEBUG, logging.WARNING, logging.ERROR, logging.CRITICAL)

# `logger.info(..., extra=REQUEST_HIGHLIGHT)` renders the whole line in the highlight color.
REQUEST_HIGHLIGHT = {"highlight": True}
# `logger.info(..., extra=STEP_HIGHLIGHT)` renders the whole line in orange (a pipeline step starts).
STEP_HIGHLIGHT = {"step": True}


def mask_cpfs_in_text(text: str) -> str:
    """Replace every CPF-shaped number in `text` by its masked form."""
    return _CPF_PATTERN.sub(lambda match: mask_cpf(match.group(0)), text)


class ContextFilter(logging.Filter):
    """Adds the `session` field and masks CPFs in the rendered message."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Add the session field and mask CPFs; never drops the record."""
        record.session = _session_var.get()
        try:
            message = record.getMessage()
        except Exception:  # malformed args: let the handler report it as usual
            return True
        masked = mask_cpfs_in_text(message)
        if masked != message:
            record.msg, record.args = masked, None
        return True


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


class ColorFormatter(logging.Formatter):
    """`key=value` lines colored for a terminal; the text is the same as the plain formatter's."""

    def format(self, record: logging.LogRecord) -> str:
        """Color request and step lines entirely, WARNING/ERROR/DEBUG lines entirely, INFO only in its level name."""
        line = super().format(record)
        if getattr(record, "highlight", False):
            return f"{HIGHLIGHT}{line}{RESET}"
        if getattr(record, "step", False):
            return f"{STEP_COLOR}{line}{RESET}"
        color = LEVEL_COLORS.get(record.levelno)
        if color is None:
            return line
        if record.levelno in WHOLE_LINE_LEVELS:
            return f"{color}{line}{RESET}"
        level = f"level={record.levelname}"
        return line.replace(level, f"level={color}{record.levelname}{RESET}", 1)


def use_color(mode: str, stream: TextIO) -> bool:
    """Decide whether to color: `always`/`never` force it; `auto` colors a terminal unless `NO_COLOR` is set."""
    mode = mode.lower()
    if mode in ("always", "never"):
        return mode == "always"
    if os.environ.get("NO_COLOR"):
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def _enable_windows_ansi() -> None:
    """Turn on ANSI escape processing in the classic Windows console (no-op elsewhere or when it fails)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        for std_handle in (-11, -12):  # stdout, stderr
            handle = kernel32.GetStdHandle(std_handle)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:  # not a console: colors are simply not rendered
        pass


@contextmanager
def log_context(session_id: str) -> Iterator[None]:
    """Tag every log line emitted inside the block (including tools and Agno internals) with `session_id`."""
    token = _session_var.set(session_id)
    try:
        yield
    finally:
        _session_var.reset(token)


def configure_logging(level: str = "INFO", color: ColorMode | str = "auto") -> None:
    """Install the single root handler (colored on a terminal) and re-route Agno's loggers to it."""
    level = level.upper()
    handler = logging.StreamHandler()
    handler.setLevel(level)  # also caps records propagated from loggers with their own level (Agno)
    if use_color(color, handler.stream):
        _enable_windows_ansi()
        handler.setFormatter(ColorFormatter(LOG_FORMAT))
    else:
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(ContextFilter())
    logging.basicConfig(level=level, handlers=[handler], force=True)

    for name in AGNO_LOGGERS:
        agno_logger = logging.getLogger(name)
        agno_logger.handlers.clear()
        agno_logger.propagate = True

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    asyncio_logger = logging.getLogger("asyncio")
    if not any(isinstance(f, WindowsSocketResetFilter) for f in asyncio_logger.filters):
        asyncio_logger.addFilter(WindowsSocketResetFilter())


def is_debug(level: str) -> bool:
    """Tell whether `level` is DEBUG (case-insensitive)."""
    return level.upper() == "DEBUG"
