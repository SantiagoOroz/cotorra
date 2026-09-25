from __future__ import annotations

from cotorra import exporters
from cotorra.models import Caption


def cap(seq: int, start: float, end: float, kind: str = "final", **texts) -> Caption:
    return Caption(
        session_id="s",
        seq=seq,
        kind=kind,
        t_start=start,
        t_end=end,
        source_lang="en",
        texts=texts or {"en": f"line {seq}"},
    )


def test_srt_structure():
    out = exporters.to_srt([cap(0, 0, 2.5, en="Hello world"), cap(1, 3, 5, en="Second")], "en")
    assert out.startswith("1\n00:00:00,000 --> 00:00:02,500\nHello world")
    assert "2\n00:00:03,000 --> 00:00:05,000\nSecond" in out


def test_vtt_has_the_required_header():
    out = exporters.to_vtt([cap(0, 0, 1.5, en="Hi")], "en")
    assert out.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.500" in out  # dots, not commas


def test_hours_are_formatted():
    out = exporters.to_srt([cap(0, 3725.5, 3728.0, en="Late in the keynote")], "en")
    assert "01:02:05,500 --> 01:02:08,000" in out


def test_partials_never_reach_an_export():
    caps = [cap(0, 0, 2, kind="partial", en="Hel"), cap(0, 0, 2.4, en="Hello")]
    assert "Hel\n" not in exporters.to_srt(caps, "en")
    assert exporters.to_txt(caps, "en").strip() == "Hello"


def test_a_resent_caption_does_not_duplicate_a_cue():
    """A worker reconnect can replay a caption; the export must stay clean."""
    caps = [cap(0, 0, 2, en="Once"), cap(0, 0, 2, en="Once")]
    assert exporters.to_srt(caps, "en").count("Once") == 1


def test_out_of_order_arrivals_are_sorted_by_time():
    out = exporters.to_txt([cap(1, 5, 7, en="second"), cap(0, 0, 2, en="first")], "en")
    assert out.splitlines() == ["first", "second"]


def test_translation_export_uses_the_requested_language():
    caps = [cap(0, 0, 2, en="Good morning", es="Buenos días")]
    assert "Buenos días" in exporters.to_srt(caps, "es")
    assert "Good morning" in exporters.to_srt(caps, "en")


def test_missing_language_falls_back_to_the_source_rather_than_dropping_the_line():
    caps = [cap(0, 0, 2, en="Only English")]
    assert "Only English" in exporters.to_srt(caps, "pt")


def test_zero_length_caption_still_gets_a_readable_duration():
    """A one-word caption with t_start == t_end would be invisible on a player."""
    out = exporters.to_srt([cap(0, 4.0, 4.0, en="Yes")], "en")
    assert "00:00:04,000 --> 00:00:04,800" in out


def test_empty_text_is_skipped():
    assert exporters.to_srt([cap(0, 0, 2, en="   ")], "en").strip() == ""


def test_json_export_round_trips():
    import json

    caps = [cap(0, 0, 2, en="Hello", es="Hola")]
    parsed = json.loads(exporters.to_json(caps, "es"))
    assert parsed[0]["texts"]["es"] == "Hola"


def test_export_rejects_an_unknown_format():
    import pytest

    with pytest.raises(ValueError, match="Unknown format"):
        exporters.export([], "es", "ass")


def test_every_declared_format_has_a_mime_type():
    assert set(exporters.FORMATS) == set(exporters.MIME)
