"""Tests for voxium.constants (env-driven defaults for polish and server)."""

import os

import voxium.constants as c


def test_env_polish_enabled_default_unset_is_true() -> None:
    os.environ.pop("VOXIUM_POLISH_ENABLED", None)
    try:
        assert c.env_polish_enabled_default() is True
    finally:
        pass


def test_env_polish_enabled_default_falsey_strings() -> None:
    for val in ("0", "false", "no", "off", "FALSE", " No "):
        os.environ["VOXIUM_POLISH_ENABLED"] = val
        try:
            assert c.env_polish_enabled_default() is False
        finally:
            os.environ.pop("VOXIUM_POLISH_ENABLED", None)


def test_env_polish_enabled_default_truthy_strings() -> None:
    for val in ("1", "true", "yes", "on"):
        os.environ["VOXIUM_POLISH_ENABLED"] = val
        try:
            assert c.env_polish_enabled_default() is True
        finally:
            os.environ.pop("VOXIUM_POLISH_ENABLED", None)


def test_env_polish_enabled_default_unknown_string_is_true() -> None:
    os.environ["VOXIUM_POLISH_ENABLED"] = "maybe"
    try:
        assert c.env_polish_enabled_default() is True
    finally:
        os.environ.pop("VOXIUM_POLISH_ENABLED", None)


def test_env_polish_empty_means_unset() -> None:
    os.environ["VOXIUM_POLISH_ENABLED"] = "   "
    try:
        assert c.env_polish_enabled_default() is True
    finally:
        os.environ.pop("VOXIUM_POLISH_ENABLED", None)
