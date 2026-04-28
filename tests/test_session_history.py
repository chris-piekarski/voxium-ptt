"""Tests for in-process session transcript history (RAM-only)."""

import io
import threading

from voxium.session_history import SessionTranscriptHistory


def test_add_and_char_cap_drops_oldest() -> None:
    h = SessionTranscriptHistory(
        max_entries=10,
        max_total_chars=5,
        max_pending_bytes=0,
    )
    h.add("ab")
    h.add("cd")
    h.add("efg")  # 2+2+3>5 → drop oldest until 2+3≤5
    assert h.text_by_display_index(1) == "efg"
    assert h.text_by_display_index(2) == "cd"


def test_entry_cap() -> None:
    h = SessionTranscriptHistory(
        max_entries=2, max_total_chars=1_000_000, max_pending_bytes=0
    )
    h.add("a")
    h.add("b")
    h.add("c")
    assert len(h) == 2
    assert h.text_by_display_index(1) == "c"
    assert h.text_by_display_index(2) == "b"
    assert h.text_by_display_index(3) is None


def test_replay_cycle() -> None:
    h = SessionTranscriptHistory(
        max_entries=10, max_total_chars=10_000, max_pending_bytes=0
    )
    h.add("first")
    h.add("second")
    r0 = h.next_replay_paste()
    assert r0 is not None
    t0, k0, n0 = r0
    assert t0 == "second" and k0 == 1 and n0 == 2
    r1 = h.next_replay_paste()
    assert r1 is not None
    t1, k1, n1 = r1
    assert t1 == "first" and k1 == 2 and n1 == 2
    r2 = h.next_replay_paste()
    assert r2 is not None
    t2, _, _ = r2
    assert t2 == "second"  # wrapped


def test_add_resets_replay() -> None:
    h = SessionTranscriptHistory(
        max_entries=10, max_total_chars=10_000, max_pending_bytes=0
    )
    h.add("a")
    h.add("b")
    h.next_replay_paste()
    h.add("c")
    r = h.next_replay_paste()
    assert r is not None
    assert r[0] == "c" and r[1] == 1


def test_pending_audio_respects_max() -> None:
    h = SessionTranscriptHistory(
        max_entries=1, max_total_chars=100, max_pending_bytes=4
    )
    bio = io.BytesIO(b"abcdef")
    h.save_pending_audio(bio)
    assert h.get_pending_audio() is None
    h.save_pending_audio(io.BytesIO(b"ab"))
    assert h.get_pending_audio() == b"ab"
    h.clear_pending_audio()
    assert h.get_pending_audio() is None


def test_format_list_empty() -> None:
    h = SessionTranscriptHistory(
        max_entries=3, max_total_chars=100, max_pending_bytes=0
    )
    assert "No transcriptions" in h.format_list_text()


def test_concurrent_add_and_list_text() -> None:
    n = 80
    h = SessionTranscriptHistory(
        max_entries=n + 10, max_total_chars=50_000, max_pending_bytes=0
    )
    errors: list[BaseException] = []

    def add_many() -> None:
        try:
            for i in range(n):
                h.add(f"line-{i} consistent phrase")
        except BaseException as e:  # pragma: no cover
            errors.append(e)

    def read_many() -> None:
        try:
            for _ in range(400):
                h.format_list_text()
        except BaseException as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=add_many), threading.Thread(target=read_many)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
    assert not errors
    assert len(h) == n


def test_empty_buffer_is_falsy_callers_should_use_is_not_none() -> None:
    """``__len__`` makes an empty ring falsy; optional handles need ``is not None``."""
    h = SessionTranscriptHistory(
        max_entries=5, max_total_chars=10_000, max_pending_bytes=0
    )
    assert len(h) == 0
    assert not h


def test_add_skips_whitespace_only() -> None:
    h = SessionTranscriptHistory(
        max_entries=5, max_total_chars=100, max_pending_bytes=0
    )
    h.add("   \n\t  ")
    assert len(h) == 0


def test_add_truncates_when_text_exceeds_max_total_chars() -> None:
    # _max_total_chars is at least 1024; use a cap above that to test truncation to the cap.
    h = SessionTranscriptHistory(
        max_entries=5, max_total_chars=1500, max_pending_bytes=0
    )
    h.add("x" * 3000)
    assert h.text_by_display_index(1) == "x" * 1500


def test_next_replay_paste_empty_buffer() -> None:
    h = SessionTranscriptHistory(
        max_entries=3, max_total_chars=100, max_pending_bytes=0
    )
    assert h.next_replay_paste() is None


def test_text_by_display_index_out_of_range() -> None:
    h = SessionTranscriptHistory(
        max_entries=3, max_total_chars=100, max_pending_bytes=0
    )
    h.add("one")
    assert h.text_by_display_index(0) is None
    assert h.text_by_display_index(2) is None


def test_format_list_preview_truncate_and_max_lines_tail() -> None:
    h = SessionTranscriptHistory(
        max_entries=30, max_total_chars=100_000, max_pending_bytes=0
    )
    long_line = "w" * 150
    h.add(long_line)
    out = h.format_list_text(max_lines=1, preview_chars=20)
    assert "…" in out
    for i in range(5):
        h.add(f"line{i}")
    out2 = h.format_list_text(max_lines=2, preview_chars=200)
    assert "older (not shown)" in out2


def test_save_pending_max_zero_short_circuits() -> None:
    h = SessionTranscriptHistory(max_entries=1, max_total_chars=10, max_pending_bytes=0)
    h.save_pending_audio(io.BytesIO(b"xx"))
    assert h.get_pending_audio() is None


def test_format_list_text_filtered_substring() -> None:
    h = SessionTranscriptHistory(
        max_entries=10, max_total_chars=10_000, max_pending_bytes=0
    )
    h.add("the quick BROWN fox")
    h.add("lazy dog")
    h.add("other")
    out = h.format_list_text_filtered("lazy")
    assert "lazy" in out and "Search" in out
    assert "#2" in out  # newest is #1 "other"; "lazy dog" is #2
    out_none = h.format_list_text_filtered("nope")
    assert "No lines match" in out_none
    assert h.format_list_text_filtered("").startswith("Add words")


def test_purge_all_clears_entries_and_pending() -> None:
    h = SessionTranscriptHistory(
        max_entries=5, max_total_chars=1_000, max_pending_bytes=1_000
    )
    h.add("a")
    h.add("b")
    h.save_pending_audio(io.BytesIO(b"RIFF..."))
    n, had = h.purge_all()
    assert n == 2 and had is True
    assert len(h) == 0
    assert h.get_pending_audio() is None
    assert h.text_by_display_index(1) is None
    n2, had2 = h.purge_all()
    assert n2 == 0 and had2 is False


def test_format_list_text_filtered_empty_buffer_with_query() -> None:
    h = SessionTranscriptHistory(
        max_entries=5, max_total_chars=1_000, max_pending_bytes=0
    )
    out = h.format_list_text_filtered("anything")
    assert "No transcriptions in this run yet" in out


def test_format_list_text_filtered_vox_tag() -> None:
    h = SessionTranscriptHistory(
        max_entries=10, max_total_chars=10_000, max_pending_bytes=0
    )
    h.add("open mic phrase", source="vox")
    out = h.format_list_text_filtered("mic")
    assert "[VOX]" in out


def test_format_list_text_filtered_match_truncates_with_ellipsis() -> None:
    """Search hit preview can shorten with an ellipsis (``…``) when over ``preview_chars``."""
    h = SessionTranscriptHistory(
        max_entries=10, max_total_chars=10_000, max_pending_bytes=0
    )
    h.add("TAILKEY" + " x" * 80)  # query at start; still long so preview must truncate
    out = h.format_list_text_filtered("TAILKEY", preview_chars=25)
    assert "…" in out
    assert "Search" in out and "#1" in out


def test_format_list_text_filtered_tail_when_many_matches() -> None:
    h = SessionTranscriptHistory(
        max_entries=50, max_total_chars=100_000, max_pending_bytes=0
    )
    for i in range(12):
        h.add(f"commonword {i} extra")
    out = h.format_list_text_filtered("commonword", max_lines=3)
    assert "more matches (not shown)" in out
