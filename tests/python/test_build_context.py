"""_build_context — the single config parser shared by the solver AND the
validator, so a drift here desyncs both. Covers the parsing that has bitten us:
zone tagging, zone/team venue blocks lowered into court sets, the shuttle
per-venue exemption, court naming, slot generation, and training requests.
"""
import pytest

from scheduler import _build_context
from helpers import make_division, make_config


# ── courts + slots ──────────────────────────────────────────────────────────

def test_court_naming_and_indices():
    cfg = make_config(
        [make_division("D", [4])],
        sites=[{"name": "Blanes", "numCourts": 2}, {"name": "Palafolls", "numCourts": 1}],
    )
    ctx = _build_context(cfg)
    names = [c["name"] for c in ctx["courts"]]
    # Global Basketball-Complex numbering: Blanes 1-6, Palafolls 7-8.
    assert names == ["Court 1", "Court 2", "Court 7"]
    assert ctx["blanes_courts"] == [0, 1]
    assert ctx["num_courts"] == 3


def test_day_slots_exclude_lunch():
    cfg = make_config(
        [make_division("D", [4])],
        setup={"nDays": 3, "lS": "13:30", "lE": "14:30",
               "dayHours": [{"start": "09:00", "end": "17:30"}] * 3},
    )
    ctx = _build_context(cfg)
    # 09:00-17:30, 90-min steps, lunch 13:30-14:30 excluded.
    assert ctx["day_slots"][0] == [540, 630, 720, 870, 960, 1050]
    assert 810 not in ctx["day_slots"][0]  # 13:30 falls in the lunch window


def test_rr_matchup_count_is_combinations_per_group():
    # one group of 4 -> C(4,2)=6 RR games.
    ctx = _build_context(make_config([make_division("D", [4])]))
    assert ctx["num_rr"] == 6
    # two groups of 3 -> 2 * C(3,2) = 6.
    ctx2 = _build_context(make_config([make_division("D", [3, 3])]))
    assert ctx2["num_rr"] == 6


# ── zones ───────────────────────────────────────────────────────────────────

def test_team_zone_parsing_filters_invalid():
    div = make_division("U12", [2])  # teams: "U12 A1", "U12 A2"
    cfg = make_config(
        [div],
        teamZones=[
            {"div": "U12", "team": "U12 A1", "zone": "white"},
            {"div": "U12", "team": "U12 A2", "zone": "BLACK"},   # case-normalised
            {"div": "U12", "team": "U12 A2", "zone": "purple"},  # invalid -> ignored
            {"div": "", "team": "x", "zone": "white"},           # no div -> ignored
        ],
    )
    ctx = _build_context(cfg)
    assert ctx["team_zone"] == {
        ("U12", "U12 A1"): "white",
        ("U12", "U12 A2"): "black",
    }


def _zone_cfg(zone_rules=None):
    """Two venues (Pineda single-court at index 0, Blanes two courts) and a
    division with one white + one black team. Used by the exemption tests."""
    div = {
        "name": "U12 MIXED", "color": "#888",
        "teams": ["WhiteTeam", "BlackTeam"],
        "manualGroups": [["WhiteTeam", "BlackTeam"]],
    }
    return make_config(
        [div],
        sites=[{"name": "Pineda", "numCourts": 1}, {"name": "Blanes", "numCourts": 2}],
        teamZones=[
            {"div": "U12 MIXED", "team": "WhiteTeam", "zone": "white"},
            {"div": "U12 MIXED", "team": "BlackTeam", "zone": "black"},
        ],
        **({"zoneVenueRules": zone_rules} if zone_rules else {}),
    )


def test_no_zone_rule_means_no_venue_is_exempt():
    ctx = _build_context(_zone_cfg())
    # Both zones can reach both venues -> nothing is single-zone -> no exemption.
    assert ctx["shuttle_exempt_venues"] == set()
    assert ctx["team_blocked_courts"] == {}


def test_zone_venue_block_lowers_into_court_set_and_exempts_venue():
    ctx = _build_context(_zone_cfg(
        zone_rules=[{"zone": "white", "venues": ["Pineda"], "mode": "mandatory"}]
    ))
    # The mandatory white->Pineda block becomes a per-team blocked court (Pineda
    # is court index 0). Blanes (main venue) would be stripped, but Pineda isn't.
    assert ctx["team_blocked_courts"][("U12 MIXED", "WhiteTeam")] == {0}
    assert ("U12 MIXED", "BlackTeam") not in ctx["team_blocked_courts"]
    # With white unable to reach Pineda, only black remains there -> single-zone
    # -> Pineda is shuttle-exempt; Blanes still has both zones.
    assert ctx["shuttle_exempt_venues"] == {"Pineda"}


def test_main_venue_is_stripped_from_zone_block():
    ctx = _build_context(_zone_cfg(
        zone_rules=[{"zone": "white", "venues": ["Blanes"], "mode": "mandatory"}]
    ))
    # Blanes is the main venue -> stripped -> rule is vacuous, no block recorded.
    assert ctx["team_blocked_courts"] == {}


# ── required training games ─────────────────────────────────────────────────

def test_team_pax_parsing_filters_invalid():
    div = make_division("U12", [2])  # teams: "U12 A1", "U12 A2"
    cfg = make_config([div], teamPax=[
        {"div": "U12", "team": "U12 A1", "pax": 32},
        {"div": "U12", "team": "U12 A2", "pax": "20"},   # numeric string -> 20
        {"div": "U12", "team": "U12 A2", "pax": 0},       # non-positive -> ignored
        {"div": "U12", "team": "U12 A1", "pax": "abc"},   # invalid -> ignored
        {"div": "", "team": "x", "pax": 10},              # no div -> ignored
        "not a dict",                                      # ignored
    ])
    ctx = _build_context(cfg)
    assert ctx["team_pax"] == {("U12", "U12 A1"): 32, ("U12", "U12 A2"): 20}


def test_no_team_pax_defaults_empty():
    ctx = _build_context(make_config([make_division("D", [4])]))
    assert ctx["team_pax"] == {}


def test_bus_seats_default_and_override():
    assert _build_context(make_config([make_division("D", [4])]))["bus_seats"] == 55
    ctx = _build_context(make_config([make_division("D", [4])], setup={"busSeats": 70}))
    assert ctx["bus_seats"] == 70


def test_required_training_games_parsed_and_defaulted():
    cfg = make_config(
        [make_division("D", [4, 3, 3, 3])],
        requiredTrainingGames=[
            {"divName": "D", "t1": "4th Group A", "preferredVenue": "Blanes", "day": "last"},
            {"divName": "D", "t1": "", "t2": "TBD"},   # no t1 -> dropped
            "not a dict",                                # dropped
        ],
    )
    ctx = _build_context(cfg)
    rtg = ctx["required_training_games"]
    assert len(rtg) == 1
    assert rtg[0]["t1"] == "4th Group A"
    assert rtg[0]["t2"] == "TBD"          # defaulted when omitted
    assert rtg[0]["preferredVenue"] == "Blanes"
    assert rtg[0]["day"] == "last"
