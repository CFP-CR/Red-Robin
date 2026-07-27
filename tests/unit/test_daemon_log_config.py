"""The daemon must make Red Robin's own ``redrobin.*`` logger tree visible.

Bug (#31): ``run_daemon_start`` called ``uvicorn.run(log_level="info")``, which
configures ONLY uvicorn's own loggers and declares no root logger, so every
``logging.getLogger("redrobin...")`` sat at effective WARNING with no handler —
27 ``logger.info``/``debug`` sites across the daemon produced nothing, and the
live smoke had to read the boot banner instead of the log. The fix builds a
``log_config`` that extends uvicorn's default with a ``redrobin`` logger.
"""

from __future__ import annotations

import logging
import logging.config

from uvicorn.config import LOGGING_CONFIG

from redrobin.cli_helpers.daemon_cmd import build_uvicorn_log_config


def test_adds_a_redrobin_logger_at_the_requested_level_with_a_real_handler():
    cfg = build_uvicorn_log_config("info")
    rr = cfg["loggers"]["redrobin"]
    assert rr["level"] == "INFO"
    assert rr["propagate"] is False
    # every handler the redrobin logger names must be defined in the config
    assert rr["handlers"], "redrobin logger has no handler — INFO would go nowhere"
    for h in rr["handlers"]:
        assert h in cfg["handlers"]


def test_preserves_uvicorns_own_loggers_and_handlers():
    cfg = build_uvicorn_log_config("info")
    # uvicorn's transport logging must survive untouched
    assert "uvicorn" in cfg["loggers"]
    assert "uvicorn.access" in cfg["loggers"]
    assert "default" in cfg["handlers"] and "access" in cfg["handlers"]
    # existing (already-imported) redrobin.* loggers must not be wiped
    assert cfg["disable_existing_loggers"] is False


def test_does_not_mutate_uvicorns_module_level_config():
    before = set(LOGGING_CONFIG["loggers"])
    cfg = build_uvicorn_log_config("info")
    cfg["loggers"]["redrobin"]["level"] = "DEBUG"  # scribble on the copy
    # the module-level template is untouched (deep copy, not a shared reference)
    assert "redrobin" not in LOGGING_CONFIG["loggers"]
    assert set(LOGGING_CONFIG["loggers"]) == before


def test_applying_the_config_actually_enables_redrobin_info():
    # the load-bearing behavior: after dictConfig, a redrobin.* child logger
    # emits at INFO (it did NOT before the fix — effective level was WARNING).
    try:
        logging.config.dictConfig(build_uvicorn_log_config("info"))
        child = logging.getLogger("redrobin.application.some.module")
        assert child.isEnabledFor(logging.INFO)
        # the record reaches a handler rather than logging.lastResort
        assert logging.getLogger("redrobin").handlers
    finally:
        # restore the redrobin logger so this global mutation can't leak into
        # other tests (e.g. pytest caplog)
        rr = logging.getLogger("redrobin")
        rr.handlers.clear()
        rr.setLevel(logging.NOTSET)
        rr.propagate = True
