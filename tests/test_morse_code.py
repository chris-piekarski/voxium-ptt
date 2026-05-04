from voxium.morse_code import (
    encode_morse_text,
    morse_marquee_for_tick,
    morse_marquee_rows_for_tick,
)


def test_encode_morse_text_words_and_punctuation() -> None:
    assert encode_morse_text("SOS copy!") == "... --- ... / -.-. --- .--. -.-- -.-.--"


def test_encode_morse_text_drops_unsupported_characters() -> None:
    assert encode_morse_text("hi ☄") == ".... .."


def test_morse_marquee_fixed_width_and_animates() -> None:
    a = morse_marquee_for_tick("go", 0, 12)
    b = morse_marquee_for_tick("go", 2, 12)

    assert len(a) == 12
    assert len(b) == 12
    assert "--. ---" in a
    assert a != b


def test_morse_marquee_rows_align_labels_and_code() -> None:
    labels, code = morse_marquee_rows_for_tick("sos", 0, 11)

    assert len(labels) == 11
    assert len(code) == 11
    assert code == "... --- ..."
    assert labels == " S   O   S "


def test_morse_marquee_blank_when_no_supported_text() -> None:
    assert morse_marquee_for_tick("☄", 0, 6) == " " * 6
    assert morse_marquee_rows_for_tick("☄", 0, 6) == (" " * 6, " " * 6)
    assert morse_marquee_for_tick("copy", 0, 0) == ""
