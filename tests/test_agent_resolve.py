"""Tests for ``voxium.agent_resolve`` — flag-precedence resolver.

Pure module: no TUI, no microphone, no MCP SDK import.
"""

from __future__ import annotations

from voxium.agent_resolve import resolve_agent_enabled


class TestResolveAgentEnabled:  # pylint: disable=too-many-public-methods
    """``resolve_agent_enabled`` follows CLI > env > YAML > default."""

    # -- CLI flag wins outright -----------------------------------------------

    def test_cli_true_wins(self) -> None:
        assert resolve_agent_enabled(cli_flag=True) is True

    def test_cli_true_wins_over_env_off(self) -> None:
        assert resolve_agent_enabled(cli_flag=True, env="0") is True

    def test_cli_true_wins_over_yaml(self) -> None:
        assert (
            resolve_agent_enabled(
                cli_flag=True,
                file_agent={"enabled": False},
            )
            is True
        )

    def test_cli_false_wins(self) -> None:
        assert resolve_agent_enabled(cli_flag=False) is False

    def test_cli_false_wins_over_env_on(self) -> None:
        assert resolve_agent_enabled(cli_flag=False, env="1") is False

    def test_cli_false_wins_over_yaml(self) -> None:
        assert (
            resolve_agent_enabled(
                cli_flag=False,
                file_agent={"enabled": True},
            )
            is False
        )

    # -- Environment variable (second) ----------------------------------------

    def test_env_one(self) -> None:
        assert resolve_agent_enabled(env="1") is True

    def test_env_lower_one(self) -> None:
        assert resolve_agent_enabled(env=" 1 ") is True

    def test_env_true(self) -> None:
        assert resolve_agent_enabled(env="true") is True

    def test_env_yes(self) -> None:
        assert resolve_agent_enabled(env="yes") is True

    def test_env_zero_off(self) -> None:
        """VOXIUM_AGENT=0 means explicitly off."""
        assert resolve_agent_enabled(env="0") is False

    def test_env_false_off(self) -> None:
        assert resolve_agent_enabled(env="false") is False

    def test_env_no_off(self) -> None:
        assert resolve_agent_enabled(env="no") is False

    def test_env_weird_value_off(self) -> None:
        """Any other set value is treated as off."""
        assert resolve_agent_enabled(env="weird-string") is False

    def test_env_unset_yaml_on(self) -> None:
        """When env is unset (None), YAML config wins."""
        assert (
            resolve_agent_enabled(
                cli_flag=None,
                env=None,
                file_agent={"enabled": True},
            )
            is True
        )

    def test_env_zero_not_yaml_off(self) -> None:
        """VOXIUM_AGENT=0 wins over YAML agent.enabled: true."""
        assert (
            resolve_agent_enabled(
                cli_flag=None,
                env="0",
                file_agent={"enabled": True},
            )
            is False
        )

    # -- YAML config (third) --------------------------------------------------

    def test_yaml_on(self) -> None:
        assert (
            resolve_agent_enabled(
                cli_flag=None,
                file_agent={"enabled": True},
            )
            is True
        )

    def test_yaml_off(self) -> None:
        assert (
            resolve_agent_enabled(
                cli_flag=None,
                file_agent={"enabled": False},
            )
            is False
        )

    def test_yaml_empty_dict(self) -> None:
        assert resolve_agent_enabled(file_agent={}) is False

    def test_yaml_none(self) -> None:
        assert resolve_agent_enabled(file_agent=None) is False

    def test_yaml_not_dict(self) -> None:
        """YAML agent field that is not a dict is treated as off."""
        assert resolve_agent_enabled(file_agent="yes") is False

    # -- Hardcoded default (fourth) -------------------------------------------

    def test_default_off(self) -> None:
        assert resolve_agent_enabled() is False

    def test_all_none(self) -> None:
        assert resolve_agent_enabled(cli_flag=None, env=None, file_agent=None) is False

    # -- _env_get override ----------------------------------------------------

    def test_env_get_override(self) -> None:
        """Custom _env_get can be used to simulate environment."""

        def fake_get(_key):
            return "1"

        assert resolve_agent_enabled(_env_get=fake_get) is True
