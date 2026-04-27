"""Repository-local path helpers (``voxium.paths``)."""

from voxium.paths import (
    default_server_log_path,
    ensure_runtime_dirs,
    history_dir,
    instance_lock_path,
    models_dir,
    repo_root,
)


def test_voxium_repo_root_respects_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOXIUM_REPO_ROOT", str(tmp_path))
    assert repo_root() == tmp_path.resolve()
    assert models_dir() == tmp_path / "models"
    assert history_dir() == tmp_path / "history"
    assert default_server_log_path().name == "voxium_server.log"
    assert instance_lock_path().name == "voxium.lock"
    assert default_server_log_path().parent == instance_lock_path().parent


def test_ensure_runtime_dirs_creates_subdirs(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOXIUM_REPO_ROOT", str(tmp_path))
    ensure_runtime_dirs()
    for name in ("models", "history", "logs"):
        p = tmp_path / name
        assert p.is_dir(), name


def test_repo_root_discovers_pyproject_without_env(monkeypatch) -> None:
    monkeypatch.delenv("VOXIUM_REPO_ROOT", raising=False)
    r = repo_root()
    assert (r / "pyproject.toml").is_file()
