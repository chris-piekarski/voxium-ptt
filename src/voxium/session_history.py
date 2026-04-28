"""
In-process PTT/VOX transcript history and last-capture buffer for re-transmit.

All data is RAM-only for this process. Oldest entries are
dropped when ``max_entries`` or ``max_total_chars`` would be exceeded.
"""

from __future__ import annotations

import io
import threading
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class _Entry:
    text: str
    source: str = "ptt"  # "ptt" | "vox"


class SessionTranscriptHistory:
    """Session-scoped transcript ring + optional pending WAV bytes for re-transmit (default F6).

    .. note:: Implements ``__len__``; an *empty* buffer is falsy in boolean context.
       Callers that need “handle exists” (vs. None) must use ``h is not None``, not ``if h``.
    """

    def __init__(
        self,
        *,
        max_entries: int,
        max_total_chars: int,
        max_pending_bytes: int,
    ) -> None:
        self._max_entries = max(1, int(max_entries))
        self._max_total_chars = max(1024, int(max_total_chars))
        self._max_pending_bytes = max(0, int(max_pending_bytes))
        self._entries: deque[_Entry] = deque()
        self._char_count = 0
        self._replay_cursor = 0
        self._pending: bytes | None = None
        # Transcribe runs in a worker thread; /history reads on the pynput thread — serialize access.
        self._lock = threading.RLock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def add(self, text: str, *, source: str = "ptt") -> None:
        """Append one transcription; resets replay cursor to latest-first."""
        t = text or ""
        if not t.strip():
            return
        if len(t) > self._max_total_chars:
            t = t[: self._max_total_chars]
        src = "vox" if (source or "").lower() == "vox" else "ptt"
        new_chars = len(t)
        with self._lock:
            self._replay_cursor = 0
            while self._entries and (
                len(self._entries) >= self._max_entries
                or self._char_count + new_chars > self._max_total_chars
            ):
                self._pop_oldest()
            self._entries.append(_Entry(text=t, source=src))
            self._char_count += new_chars

    def _pop_oldest(self) -> None:
        e = self._entries.popleft()
        self._char_count -= len(e.text)

    def next_replay_paste(self) -> tuple[str, int, int] | None:
        """
        Text for the next recovery hotkey paste, then advance the cycle.

        Returns ``(text, k, n)`` where *n* is total entries and *k* is 1-based from
        newest (1 = latest transmission). ``None`` if empty.
        """
        with self._lock:
            n = len(self._entries)
            if n == 0:
                return None
            i = self._replay_cursor % n
            text = self._entries[-(i + 1)].text
            k = i + 1
            self._replay_cursor = (self._replay_cursor + 1) % n
            return (text, k, n)

    def text_by_display_index(self, display_num: int) -> str | None:
        """
        ``display_num`` is 1-based with **1 = most recent** (latest PTT result).
        """
        with self._lock:
            if display_num < 1:
                return None
            n = len(self._entries)
            if display_num > n:
                return None
            return self._entries[-display_num].text

    def format_list_text(self, *, max_lines: int = 25, preview_chars: int = 100) -> str:
        with self._lock:
            if not self._entries:
                return (
                    "No transcriptions in this run yet, copy.\n"
                    "\n"
                    "  After a good PTT, /history shows lines with #1 = most recent, copy.\n"
                    "  This list is RAM-only (not on disk).\n"
                    "  Short or junk-like text is filtered — use a clear phrase to get a line here."
                )
            lines: list[str] = [
                "  #1 = most recent; higher # = older — [PTT] = push-to-talk, [VOX] = open-mic utterance, copy.",
                "",
            ]
            rev = list(reversed(self._entries))
            shown = rev[:max_lines]
            for i, e in enumerate(shown, start=1):
                t = e.text.replace("\n", " ").strip()
                if len(t) > preview_chars:
                    t = t[: preview_chars - 1] + "…"
                tag = "VOX" if getattr(e, "source", "ptt") == "vox" else "PTT"
                lines.append(f"  📋  #{i}  [{tag}]  {t}")
            lines.append("")
            tail = ""
            if len(rev) > max_lines:
                tail = f"  … and {len(rev) - max_lines} older (not shown), copy.\n"
            lines.append(
                tail
                + "  /history copy <n> — same #n (1 = most recent), copy.\n"
                + "  /history search <text> — filter; /history clear — wipe, copy."
            )
            return "\n".join(lines)

    def format_list_text_filtered(
        self,
        query: str,
        *,
        max_lines: int = 25,
        preview_chars: int = 100,
    ) -> str:
        """
        Like :meth:`format_list_text`, but only entries whose text contains *query* (case-insensitive).

        ``#n`` matches the full list (``#1`` = most recent) so ``/history copy n`` uses the same number.
        """
        q = (query or "").strip()
        if not q:
            return "Add words to search after /history search — e.g. /history search meeting notes, copy."
        q_lower = q.lower()
        with self._lock:
            if not self._entries:
                return (
                    "No transcriptions in this run yet, copy.\n"
                    "\n"
                    "  After a good PTT, /history uses #1 = most recent, copy.\n"
                    "  This list is RAM-only (not on disk).\n"
                    "  Short or junk-like text is filtered — use a clear phrase to get a line here."
                )
            rev = list(reversed(self._entries))
            matches: list[tuple[int, str]] = []
            for i, e in enumerate(rev, start=1):
                t = e.text.replace("\n", " ").strip()
                if q_lower in t.lower():
                    prev = t
                    if len(prev) > preview_chars:
                        prev = prev[: preview_chars - 1] + "…"
                    matches.append((i, prev))
        if not matches:
            return (
                f"No lines match {q!r} in this session buffer, copy.\n"
                "\n"
                "  Use /history for the full list, or try different words, copy."
            )
        lines: list[str] = [
            f"Search {q!r} — {len(matches)} hit(s) (order: most recent first), copy.",
            "  #n is the line number in the full /history list (#1 = most recent), copy.",
            "",
        ]
        shown = matches[:max_lines]
        for display_num, prev in shown:
            # Not `e`: that name is still bound from `for i, e in enumerate(rev)` above (leaked loop var).
            hit_entry = (
                self._entries[-display_num]
                if 1 <= display_num <= len(self._entries)
                else None
            )
            tag = "PTT"
            if hit_entry is not None and getattr(hit_entry, "source", "ptt") == "vox":
                tag = "VOX"
            lines.append(f"  📋  #{display_num}  [{tag}]  {prev}")
        lines.append("")
        tail = ""
        if len(matches) > max_lines:
            tail = (
                f"  … and {len(matches) - max_lines} more matches (not shown), copy.\n"
            )
        lines.append(
            tail
            + "  /history copy <n> — same #n as the full list (1 = most recent), copy."
        )
        return "\n".join(lines)

    def save_pending_audio(self, wav_buffer: io.BytesIO) -> None:
        """Keep last WAV bytes in RAM if under the cap; otherwise drop."""
        wav_buffer.seek(0)
        b = wav_buffer.read()
        wav_buffer.seek(0)
        with self._lock:
            if self._max_pending_bytes <= 0:
                self._pending = None
                return
            if len(b) > self._max_pending_bytes:
                self._pending = None
                return
            self._pending = b

    def clear_pending_audio(self) -> None:
        with self._lock:
            self._pending = None

    def purge_all(self) -> tuple[int, bool]:
        """
        Remove all transcript lines and pending WAV; reset the replay cursor.

        Returns ``(n_lines_removed, had_pending_wav)`` for operator feedback.
        """
        with self._lock:
            n = len(self._entries)
            had_pending = self._pending is not None
            self._entries.clear()
            self._char_count = 0
            self._replay_cursor = 0
            self._pending = None
            return n, had_pending

    def get_pending_audio(self) -> bytes | None:
        with self._lock:
            return self._pending
