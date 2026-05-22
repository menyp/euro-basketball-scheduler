"""_time_to_min / _min_to_time — small but used everywhere (slot math, validator,
training placement). A regression here corrupts every downstream time check."""
import pytest

from scheduler import _time_to_min, _min_to_time


@pytest.mark.parametrize("text,minutes", [
    ("00:00", 0),
    ("09:00", 540),
    ("13:30", 810),
    ("14:30", 870),
    ("17:30", 1050),
    ("19:00", 1140),
    ("23:59", 1439),
])
def test_time_to_min(text, minutes):
    assert _time_to_min(text) == minutes


def test_time_to_min_handles_empty_and_none():
    assert _time_to_min("") == 0
    assert _time_to_min(None) == 0


def test_time_to_min_handles_hour_only():
    assert _time_to_min("9") == 540


@pytest.mark.parametrize("minutes,text", [
    (0, "00:00"),
    (540, "09:00"),
    (870, "14:30"),
    (1050, "17:30"),
    (1140, "19:00"),
])
def test_min_to_time_zero_pads(minutes, text):
    assert _min_to_time(minutes) == text


@pytest.mark.parametrize("text", ["09:00", "10:30", "12:00", "14:30", "16:00", "17:30"])
def test_round_trip(text):
    assert _min_to_time(_time_to_min(text)) == text
