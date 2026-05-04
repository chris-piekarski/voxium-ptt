"""Morse-code helpers for the standby telemetry marquee."""

from __future__ import annotations

MORSE_ALPHABET: dict[str, str] = {
    "A": ".-",
    "B": "-...",
    "C": "-.-.",
    "D": "-..",
    "E": ".",
    "F": "..-.",
    "G": "--.",
    "H": "....",
    "I": "..",
    "J": ".---",
    "K": "-.-",
    "L": ".-..",
    "M": "--",
    "N": "-.",
    "O": "---",
    "P": ".--.",
    "Q": "--.-",
    "R": ".-.",
    "S": "...",
    "T": "-",
    "U": "..-",
    "V": "...-",
    "W": ".--",
    "X": "-..-",
    "Y": "-.--",
    "Z": "--..",
    "0": "-----",
    "1": ".----",
    "2": "..---",
    "3": "...--",
    "4": "....-",
    "5": ".....",
    "6": "-....",
    "7": "--...",
    "8": "---..",
    "9": "----.",
    ".": ".-.-.-",
    ",": "--..--",
    "?": "..--..",
    "!": "-.-.--",
    "'": ".----.",
    '"': ".-..-.",
    "/": "-..-.",
    "(": "-.--.",
    ")": "-.--.-",
    "&": ".-...",
    ":": "---...",
    ";": "-.-.-.",
    "=": "-...-",
    "+": ".-.-.",
    "-": "-....-",
    "_": "..--.-",
    "$": "...-..-",
    "@": ".--.-.",
}


def encode_morse_text(text: str, *, max_chars: int = 160) -> str:
    """Encode text as ITU-style Morse tokens; words are separated by ``/``."""
    raw = (text or "").upper()
    if max_chars > 0:
        raw = raw[:max_chars]
    words: list[str] = []
    for word in raw.split():
        tokens = [MORSE_ALPHABET[ch] for ch in word if ch in MORSE_ALPHABET]
        if tokens:
            words.append(" ".join(tokens))
    return " / ".join(words)


def _labeled_morse_rows(text: str, *, max_chars: int = 160) -> tuple[str, str]:
    raw = (text or "").upper()
    if max_chars > 0:
        raw = raw[:max_chars]
    label_words: list[str] = []
    code_words: list[str] = []
    for word in raw.split():
        labels: list[str] = []
        codes: list[str] = []
        for ch in word:
            code = MORSE_ALPHABET.get(ch)
            if code is None:
                continue
            codes.append(code)
            labels.append(ch.center(len(code)))
        if codes:
            label_words.append(" ".join(labels))
            code_words.append(" ".join(codes))
    return " / ".join(label_words), " / ".join(code_words)


def morse_marquee_for_tick(text: str, tick: int, width: int) -> str:
    """Return a fixed-width scrolling Morse-code window for the standby box."""
    _, code = morse_marquee_rows_for_tick(text, tick, width)
    return code


def morse_marquee_rows_for_tick(text: str, tick: int, width: int) -> tuple[str, str]:
    """Return aligned ``(character_labels, morse_code)`` scrolling rows."""
    w = max(0, int(width))
    if w <= 0:
        return "", ""
    labels, encoded = _labeled_morse_rows(text)
    if not encoded:
        blank = " " * w
        return blank, blank
    loop_labels = f"{labels}   "
    loop = f"{encoded}   "
    reps = (w // len(loop)) + 3
    label_belt = loop_labels * reps
    belt = loop * reps
    start = int(tick) % len(loop)
    label_window = (label_belt[start:] + label_belt[:start])[:w]
    window = (belt[start:] + belt[:start])[:w]
    if len(label_window) < w:
        label_window = label_window.ljust(w)
    if len(window) < w:
        window = window.ljust(w)
    return label_window, window
