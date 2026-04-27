"""Rotating CB/HAM readback lines."""
from voxium import radio_readback as rb


def test_readback_count_and_cycle() -> None:
    assert rb.readback_phrase_count() == 25
    rb.reset_readback_cycler()
    first = rb.take_readback()
    seen = {first}
    for _ in range(24):
        s = rb.take_readback()
        assert s not in seen
        seen.add(s)
    assert rb.take_readback() == first


def test_rexmit_suffix() -> None:
    rb.reset_readback_cycler()
    a = rb.take_readback_rexmit()
    assert a.endswith(" (re-transmit)")
    assert "10-4" in a or "Roger" in a or "copy" in a.lower() or "log" in a.lower()
