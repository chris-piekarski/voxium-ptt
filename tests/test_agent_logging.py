"""Tests for ``voxium.agent_logging`` — SDK log guard.

Pure module: no MCP SDK import, no network, no microphone.
"""

from __future__ import annotations

import logging

import pytest

from voxium.agent_logging import install_sdk_log_guard


@pytest.fixture(autouse=True)
def _restore_root_handlers():
    """Save and restore root logger handlers around each test."""
    root_logger = logging.getLogger()
    saved = list(root_logger.handlers)
    # NOTE: we do NOT clear here — pytest log capture re-adds handlers
    # after fixture setup.  Tests that need an empty handler list do
    # that explicitly in the test body.
    yield
    root_logger.handlers.clear()
    root_logger.handlers.extend(saved)


class TestInstallSdkLogGuard:
    """``install_sdk_log_guard`` appends one ``NullHandler`` when root logger is empty."""

    def test_appends_null_handler_when_empty(self, _restore_root_handlers) -> None:
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        install_sdk_log_guard()
        assert len(root_logger.handlers) == 1
        assert isinstance(root_logger.handlers[0], logging.NullHandler)

    def test_no_op_when_handlers_present(self, _restore_root_handlers) -> None:
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        root_logger.addHandler(logging.StreamHandler())
        install_sdk_log_guard()
        assert len(root_logger.handlers) == 1

    def test_second_call_is_no_op(self, _restore_root_handlers) -> None:
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        install_sdk_log_guard()
        count = len(root_logger.handlers)
        install_sdk_log_guard()
        assert len(root_logger.handlers) == count

    def test_preserves_existing_handlers(self, _restore_root_handlers) -> None:
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        h1 = logging.StreamHandler()
        root_logger.addHandler(h1)
        install_sdk_log_guard()
        assert len(root_logger.handlers) == 1  # no-op: existing handler preserved
