"""
End-pointing for VOX (open-mic) mode: accumulate speech, end on a **sustained** low-energy run
(“hangover” in seconds) with hysteresis: **per-frame** RMS for end-of-utterance, optional EMA
when ``min_speech_frames_to_start`` > 1 for a steadier *start*.

**Design goals**

- **Natural phrasing** — default end-of-utterance hang is **~2.4s** of weighted low energy, so a
  1–2s gap *between words* in the same thought usually does not fire a cut; only a clear
  end-of-turn pause does. While **any** frame is at/above the speech level, the silence timer
  resets (operator still talking). Brief dips in the *uncertain* band add fractional weight only.
- **Stable start** — optional N-frame streak (and EMA when N>1) to reject single-frame transients
  and shallow ramps into the first syllable.
- **Stable end** — frames at or below the **silence** floor get full time credit; the fuzzy band
  between silence and speech gets partial credit; at/above the speech level resets the timer.

Callers can apply :func:`voxium.speech_guards.has_speech` before remote transcription. Buffers are not
persisted beyond the chunker’s working set.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

Frame = npt.NDArray[np.float32]  # 1D float32 mono


def _rms(x: np.ndarray) -> float:
    if x.size < 2:
        return 0.0
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))


class UtteranceChunker:
    """Mono float32 stream @ *sample_rate* Hz; :meth:`feed` returns 0+ completed chunks."""

    def __init__(
        self,
        sample_rate: int,
        *,
        frame_ms: float = 30.0,
        speech_rms: float = 0.012,
        silence_rms: float = 0.0065,
        hangover_ms: float = 2400.0,
        min_utterance_ms: float = 300.0,
        max_utterance_ms: float = 60_000.0,
        pre_roll_ms: float = 300.0,
        ema_alpha: float = 0.42,
        min_speech_frames_to_start: int = 1,
        uncertain_silence_weight: float = 0.2,
    ) -> None:
        if uncertain_silence_weight < 0.0 or uncertain_silence_weight > 1.0:
            raise ValueError("uncertain_silence_weight must be in [0, 1]")
        self._sr = max(1, int(sample_rate))
        self._frame_ms = float(max(5.0, frame_ms))
        self._fn = max(1, int(self._sr * self._frame_ms / 1000.0))
        self._sp_th = float(speech_rms)
        # Strict floor: only clearly quiet frames get a *full* step toward end (see uncertain band).
        self._sil_th = float(min(silence_rms, self._sp_th * 0.9))
        self._hang_s = max(self._frame_ms / 1000.0, float(hangover_ms) / 1000.0)
        # Silence budget (seconds) — same scale as ``_silence_add_for_frame``.
        self._hang_sil_budget = self._hang_s
        self._min_n = max(1, int(self._sr * min_utterance_ms / 1000.0))
        self._max_n = max(self._min_n, int(self._sr * max_utterance_ms / 1000.0))
        pre_frames = (self._sr * pre_roll_ms / 1000.0) / self._fn
        self._pre_n = int(math.ceil(max(1.0, pre_frames)))

        # When ``min_speech_frames_to_start`` > 1, EMA-smoothed level must also exceed the speech
        # threshold so a single spiky frame at the start of a sine-like ramp does not count.
        self._ema_alpha = max(0.05, min(0.9, float(ema_alpha)))
        self._min_start = max(1, int(min_speech_frames_to_start))
        self._w_uncertain = float(uncertain_silence_weight)

        self._spill: npt.NDArray[np.float32] = np.empty(0, dtype=np.float32)
        self._pre: list[Frame] = []
        self._in = False
        self._utt: npt.NDArray[np.float32] = np.empty(0, dtype=np.float32)
        self._sil_accum = 0.0
        self._ema: float | None = None
        self._pre_speech_streak = 0
        self._hud = "idle"
        self._emitted = 0
        # Minimum voiced span (seconds) before a pause can end a segment.
        self._min_speech_s = max(0.0, 0.2)

    @property
    def chunks_emitted(self) -> int:
        return self._emitted

    def state_label(self) -> str:
        if self._in and self._sil_accum > 0.0:
            return "gating"
        if self._in:
            return "utterance"
        return "listening"

    def _update_ema_start(self, r: float) -> float:
        a = self._ema_alpha
        if self._ema is None:
            self._ema = float(r)
        else:
            self._ema = a * r + (1.0 - a) * self._ema
        return self._ema

    def _speech_loud_for_start(self, r: float) -> bool:
        """``r`` must clear the speech level; with multi-frame start, EMA must agree (ramp / click)."""
        if r < self._sp_th:
            return False
        if self._min_start <= 1:
            return True
        e = self._update_ema_start(r)
        return e >= self._sp_th

    def _concat_pre(self) -> Frame:
        if not self._pre:
            return np.empty(0, dtype=np.float32)
        o = np.concatenate(self._pre) if len(self._pre) > 1 else self._pre[0].copy()
        self._pre = []
        return o

    def _emit_trim_tail(self, raw: np.ndarray) -> np.ndarray | None:
        """Remove the trailing end pause (≈ hangover); require minimum length."""
        # A silence run of at least the hangover budget was required to end; drop most of that
        # duration from the tail so the file is not padded with the pause that tripped the gate.
        if raw.size < 1:
            return None
        tail = int(
            min(
                max(0, raw.size - 1),
                round(0.88 * self._hang_s * self._sr),
            )
        )
        t = raw[: raw.size - tail] if tail else raw
        if t.size >= self._min_n:
            return t
        return None

    def _silence_add_for_frame(self, r: float) -> None:
        """Map **per-frame** RMS to silence weight (end-of-utterance; fast response on real silence).

        **Speech** clears the timer. **Deep silence** (at/under the silence floor) accrues full time.
        The fuzzy band between the two: the **upper** half (still “active-ish” / breath near speech)
        does not add end credit (only a slight decay on the running sum); the lower half uses
        ``uncertain_silence_weight`` so a long breathy tail can still close after the main hang.
        """
        frame_t = self._frame_ms / 1000.0
        if r >= self._sp_th:
            self._sil_accum = 0.0
            return
        if r <= self._sil_th:
            self._sil_accum += frame_t
            return
        # Uncertain: nearer to speech than to the silence floor — treat as ongoing activity, not a
        # phrase-final pause (no net build toward the hang budget).
        mid = 0.5 * (self._sil_th + self._sp_th)
        if r >= mid:
            self._sil_accum *= 0.92
            return
        if self._w_uncertain > 0.0:
            self._sil_accum += self._w_uncertain * frame_t

    def feed(self, samples: Frame) -> list[Frame]:
        s = np.asarray(samples, dtype=np.float32, order="C").ravel()
        if s.size:
            self._spill = (
                s if not self._spill.size else np.concatenate([self._spill, s])
            )
        done: list[Frame] = []
        while self._spill.size >= self._fn:
            fr = self._spill[: self._fn]
            self._spill = self._spill[self._fn :]
            r = _rms(fr)
            if not self._in:
                loud = self._speech_loud_for_start(r)
                self._pre.append(fr)
                if len(self._pre) > self._pre_n:
                    self._pre.pop(0)
                if loud:
                    self._pre_speech_streak += 1
                else:
                    self._pre_speech_streak = 0
                    if self._min_start > 1:
                        self._ema = None
                if self._pre_speech_streak >= self._min_start and loud:
                    pre = self._concat_pre()
                    if pre.size and fr.size:
                        self._utt = np.concatenate([pre, fr])
                    elif pre.size:
                        self._utt = pre.copy()
                    else:
                        self._utt = fr.copy()
                    self._in = True
                    self._sil_accum = 0.0
                    self._pre_speech_streak = 0
                continue

            # In utterance: instant RMS for end-of-utterance (``_silence_add_for_frame``), not EMA.
            self._utt = fr if not self._utt.size else np.concatenate([self._utt, fr])
            if self._utt.size > self._max_n:
                chunk = self._utt[: self._max_n].copy()
                self._utt = self._utt[self._max_n :]
                self._sil_accum = 0.0
                if chunk.size >= self._min_n:
                    done.append(chunk)
                    self._emitted += 1
                    self._ema = None

            self._silence_add_for_frame(r)

            # Do not end before we have enough *voiced* span (avoids spurious very short “words”).
            if self._utt.size < int(self._sr * self._min_speech_s):
                continue

            if self._sil_accum >= self._hang_sil_budget:
                t = self._emit_trim_tail(self._utt)
                self._in = False
                self._ema = None
                self._utt = np.empty(0, dtype=np.float32)
                self._sil_accum = 0.0
                if t is not None and t.size >= self._min_n:
                    done.append(t.astype(np.float32, copy=False))
                    self._emitted += 1
        return done
