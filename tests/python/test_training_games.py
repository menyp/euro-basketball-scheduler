"""_place_training_games — pairs same-division blank-day teams into a training
game and places them on a legal slot. Pins the venue-block intersection that was
fixed in e8379f0 (a White team's training game must not land on a blocked court).
"""
import pytest

from scheduler import _build_context, _place_training_games
from helpers import make_config


def _cfg(zone_rules=None, zones=None):
    """Pineda (single court, index 0) ordered BEFORE Blanes so an unblocked pair
    naturally lands at Pineda first — that makes the block test meaningful."""
    div = {
        "name": "U12 MIXED", "color": "#888",
        "teams": ["Alpha", "Beta"],
        "manualGroups": [["Alpha", "Beta"]],
    }
    extra = {}
    if zones:
        extra["teamZones"] = zones
    if zone_rules:
        extra["zoneVenueRules"] = zone_rules
    return make_config(
        [div],
        sites=[{"name": "Pineda", "numCourts": 1}, {"name": "Blanes", "numCourts": 2}],
        **extra,
    )


EMPTY_ASSEMBLED = {"sched": {"gameDays": []}}
BLANKS = [
    {"divName": "U12 MIXED", "team": "Alpha", "day": 0},
    {"divName": "U12 MIXED", "team": "Beta", "day": 0},
]


def test_pairs_two_blanks_into_one_training_game():
    ctx = _build_context(_cfg())
    games, remaining = _place_training_games(EMPTY_ASSEMBLED, BLANKS, ctx)
    assert len(games) == 1
    g = games[0]
    assert {g["t1"], g["t2"]} == {"Alpha", "Beta"}
    assert g["isTraining"] is True
    assert remaining == []
    # Pineda is index 0 and free, so an unblocked pair lands there.
    assert g["loc"] == "Pineda"


def test_venue_block_keeps_training_game_off_blocked_court():
    # Both teams are White and White is mandatorily blocked from Pineda.
    ctx = _build_context(_cfg(
        zones=[
            {"div": "U12 MIXED", "team": "Alpha", "zone": "white"},
            {"div": "U12 MIXED", "team": "Beta", "zone": "white"},
        ],
        zone_rules=[{"zone": "white", "venues": ["Pineda"], "mode": "mandatory"}],
    ))
    games, remaining = _place_training_games(EMPTY_ASSEMBLED, BLANKS, ctx)
    assert len(games) == 1
    g = games[0]
    # The fix: the blocked venue must be excluded even though it sorts first.
    assert g["loc"] != "Pineda"
    assert g["loc"] == "Blanes"
    assert remaining == []


def test_no_blanks_returns_empty():
    ctx = _build_context(_cfg())
    games, remaining = _place_training_games(EMPTY_ASSEMBLED, [], ctx)
    assert games == []
    assert remaining == []


def test_odd_blank_falls_through_to_tbd_training_game():
    # A single unpaired blank gets a TBD training game (not silently dropped).
    ctx = _build_context(_cfg())
    one = [{"divName": "U12 MIXED", "team": "Alpha", "day": 0}]
    games, remaining = _place_training_games(EMPTY_ASSEMBLED, one, ctx)
    assert len(games) == 1
    assert games[0]["t1"] == "Alpha"
    assert games[0]["t2"] == "TBD"
    assert remaining == []
