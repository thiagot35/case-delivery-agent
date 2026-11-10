import io
import logging

import pytest

from delivery_agent.logging_config import (
    ContextFilter,
    configure_logging,
    is_debug,
    log_context,
    mask_cpfs_in_text,
)


@pytest.fixture
def restore_logging():
    """Restore the root and Agno logger configuration after the test."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    agno = logging.getLogger("agno")
    saved_agno = (agno.handlers[:], agno.propagate, agno.level)
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    agno.handlers[:], agno.propagate, agno.level = saved_agno[0], saved_agno[1], saved_agno[2]


def _capture() -> io.StringIO:
    """Point the root handler configured by configure_logging() at a buffer."""
    stream = io.StringIO()
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(stream)
    return stream


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Valid CPF numbers here are random test values generated via https://www.geradordecpf.org/ (no real person);
        # the invalid ones are made up to fail validation.
        ("user_id=52998224725", "user_id=***.***.*47-25"),
        ("cpf 529.982.247-25 ok", "cpf ***.***.*47-25 ok"),
        ('{"client_document": "52998224725"}', '{"client_document": "***.***.*47-25"}'),
        ("'current_user_id': '52998224725'}", "'current_user_id': '***.***.*47-25'}"),
    ],
)
def test_mask_cpfs_in_text(text, expected):
    """CPFs are masked in plain, punctuated, JSON and dict-repr text."""
    assert mask_cpfs_in_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "order_id=12 total=R$ 94,00",
        "run 550e8400-e29b-41d4-a716-446655440000",
        "a12345678901",  # glued to a letter: not a CPF
        "529982247250",  # 12 digits
        "already masked ***.***.*47-25",
    ],
)
def test_mask_cpfs_in_text_leaves_other_values(text):
    """Ids, prices, UUIDs and already-masked values are left untouched."""
    assert mask_cpfs_in_text(text) == text


def test_filter_masks_formatted_args_and_adds_session():
    """The filter masks CPFs passed as args and adds the session field."""
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "customer=%s", ("52998224725",), None)
    with log_context("session-1"):
        assert ContextFilter().filter(record)
    assert record.getMessage() == "customer=***.***.*47-25"
    assert record.session == "session-1"


def test_session_defaults_to_dash_outside_a_conversation():
    """Outside a conversation the session field is '-'."""
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello", None, None)
    ContextFilter().filter(record)
    assert record.session == "-"


def test_agno_logs_go_through_the_app_handler(restore_logging):
    """Agno debug lines use the app handler, with session and CPF masking."""
    configure_logging("DEBUG")
    stream = _capture()
    agno = logging.getLogger("agno")
    agno.setLevel(logging.DEBUG)  # what Agent(debug_mode=True) does on every run

    with log_context("s-42"):
        agno.debug("<session_state> {'current_user_id': '52998224725'} </session_state>")

    line = stream.getvalue()
    assert "logger=agno session=s-42" in line
    assert "52998224725" not in line
    assert "***.***.*47-25" in line


def test_handler_level_caps_propagated_agno_records(restore_logging):
    """The handler level drops Agno INFO lines when LOG_LEVEL is WARNING."""
    configure_logging("WARNING")
    stream = _capture()
    logging.getLogger("agno").info("Agent Run Start")  # agno logger itself is at INFO
    assert stream.getvalue() == ""


def test_is_debug():
    """is_debug matches DEBUG case-insensitively."""
    assert is_debug("debug") and is_debug("DEBUG")
    assert not is_debug("INFO")


def _asyncio_record(msg: str, exc: BaseException) -> logging.LogRecord:
    """Build an asyncio error record carrying `exc`."""
    return logging.LogRecord("asyncio", logging.ERROR, __file__, 1, msg, None, (type(exc), exc, None))


def test_windows_socket_reset_is_dropped(restore_logging):
    """The Proactor `_call_connection_lost` reset traceback is filtered; other asyncio errors are kept."""
    from delivery_agent.logging_config import WindowsSocketResetFilter

    configure_logging("INFO")
    assert any(isinstance(f, WindowsSocketResetFilter) for f in logging.getLogger("asyncio").filters)
    noise = _asyncio_record(
        "Exception in callback _ProactorBasePipeTransport._call_connection_lost()", ConnectionResetError(10054)
    )
    other = _asyncio_record("Task exception was never retrieved", ConnectionResetError(10054))
    assert not WindowsSocketResetFilter().filter(noise)
    assert WindowsSocketResetFilter().filter(other)


# --- Colored terminal output ------------------------------------------------------------------------------


def _format(level: int, msg: str, highlight: bool = False) -> str:
    """Render one record with the color formatter."""
    from delivery_agent.logging_config import LOG_FORMAT, ColorFormatter

    record = logging.LogRecord("x", level, __file__, 1, msg, None, None)
    record.session = "-"
    if highlight:
        record.highlight = True
    return ColorFormatter(LOG_FORMAT).format(record)


def test_step_line_is_orange():
    """A step record is wrapped entirely in orange, a different color from the request line."""
    from delivery_agent.logging_config import HIGHLIGHT, LOG_FORMAT, RESET, STEP_COLOR, ColorFormatter

    record = logging.LogRecord("x", logging.INFO, __file__, 1, "step=filter-input", None, None)
    record.session = "-"
    record.step = True
    line = ColorFormatter(LOG_FORMAT).format(record)
    assert line.startswith(STEP_COLOR) and line.endswith(RESET)
    assert STEP_COLOR != HIGHLIGHT and "msg=step=filter-input" in line


def test_request_line_is_highlighted():
    """A highlighted record is wrapped entirely in the request color."""
    from delivery_agent.logging_config import HIGHLIGHT, RESET

    line = _format(logging.INFO, "request POST /messages", highlight=True)
    assert line.startswith(HIGHLIGHT) and line.endswith(RESET)
    assert "level=INFO" in line and "msg=request POST /messages" in line


def test_info_colors_only_the_level_name():
    """INFO lines keep the message uncolored and color only the level name."""
    from delivery_agent.logging_config import LEVEL_COLORS, RESET

    line = _format(logging.INFO, "intent=delivery")
    assert f"level={LEVEL_COLORS[logging.INFO]}INFO{RESET}" in line
    assert line.endswith("msg=intent=delivery")


@pytest.mark.parametrize("level", [logging.DEBUG, logging.WARNING, logging.ERROR, logging.CRITICAL])
def test_other_levels_color_the_whole_line(level):
    """DEBUG (dim), WARNING, ERROR and CRITICAL lines are colored entirely."""
    from delivery_agent.logging_config import LEVEL_COLORS, RESET

    line = _format(level, "something")
    assert line.startswith(LEVEL_COLORS[level]) and line.endswith(RESET)


def test_use_color(monkeypatch):
    """auto colors only a terminal without NO_COLOR; always/never force it."""
    from delivery_agent.logging_config import use_color

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            """Pretend to be a terminal."""
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    assert use_color("auto", _Tty()) is True
    assert use_color("auto", io.StringIO()) is False  # piped / redirected
    assert use_color("always", io.StringIO()) is True
    assert use_color("never", _Tty()) is False
    monkeypatch.setenv("NO_COLOR", "1")
    assert use_color("auto", _Tty()) is False


def test_plain_output_has_no_escape_codes(restore_logging):
    """Without a terminal the lines stay plain key=value, even for highlighted records."""
    configure_logging("INFO", "never")
    stream = _capture()
    logging.getLogger("x").info("request POST /messages", extra={"highlight": True})
    assert "\033[" not in stream.getvalue()
    assert "msg=request POST /messages" in stream.getvalue()


def test_always_installs_the_color_formatter(restore_logging):
    """LOG_COLOR=always uses the color formatter."""
    from delivery_agent.logging_config import ColorFormatter

    configure_logging("INFO", "always")
    assert isinstance(logging.getLogger().handlers[0].formatter, ColorFormatter)
