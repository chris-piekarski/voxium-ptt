"""Repository disk usage text (``make disk-usage``, ``/disk``)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from voxium.disk_usage_report import (
    VOXIUM_DATA_DIR_NAMES,
    _dir_size,
    _human_size,
    _iter_files,
    _line_for_path,
    format_repo_disk_usage_text,
)


def test_format_repo_disk_usage_text_counts_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("voxium.disk_usage_report.repo_root", lambda: tmp_path)
    (tmp_path / "models" / "sub").mkdir(parents=True)
    (tmp_path / "models" / "sub" / "a.bin").write_bytes(b"x" * 5000)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "a.log").write_text("x")
    out = format_repo_disk_usage_text()
    assert "=== Voxium local data (repository) ===" in out
    for name in VOXIUM_DATA_DIR_NAMES:
        assert f"--- {name}/ ---" in out
    assert "K\t" in out  # models/ subtree (non-trivial size)
    assert str(tmp_path / "models") in out
    assert str(tmp_path / "logs") in out


def test_human_size_bytes_and_gigabytes() -> None:
    assert _human_size(0) == "0"
    assert _human_size(500) == "500"
    assert _human_size(3 * 1024 * 1024).endswith("M")
    assert _human_size(2 * 1024**3).endswith("G")


def test_iter_files_file_vs_empty_not_dir(tmp_path) -> None:
    f = tmp_path / "solo.bin"
    f.write_bytes(b"ab")
    assert list(_iter_files(f)) == [f]
    assert list(_iter_files(tmp_path / "nope")) == []


def test_iter_files_rglob_oserror(tmp_path) -> None:
    p = tmp_path / "d"
    p.mkdir()
    with patch.object(Path, "rglob", side_effect=OSError("denied")):
        assert list(_iter_files(p)) == []


def test_dir_size_file_stat_fails() -> None:
    p = MagicMock(spec=Path)
    p.is_file.return_value = True
    p.stat.side_effect = OSError("unreadable")
    assert _dir_size(p) == 0


def test_dir_size_directory_skips_unstatable_file(tmp_path, monkeypatch) -> None:
    (tmp_path / "a.bin").write_bytes(b"12")
    orig_stat = Path.stat

    def picky_stat(self):
        if self.name == "a.bin":
            raise OSError("bad")
        return orig_stat(self)

    monkeypatch.setattr(Path, "stat", picky_stat)
    assert _dir_size(tmp_path) == 0


def test_line_for_path_exists(tmp_path) -> None:
    p = tmp_path / "m" / "x"
    p.parent.mkdir(parents=True)
    p.write_text("x")
    line = _line_for_path(p)
    assert "\t" in line and str(p) in line
