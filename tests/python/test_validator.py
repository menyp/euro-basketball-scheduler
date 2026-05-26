"""validate_schedule — checks a (possibly hand-edited) flat game list against the
setup, reusing _build_context so its rules can't drift from the solver. We assert
on individual named checks (a fully rule-clean tournament is hard to hand-build),
proving each rule both fires on a real violation and stays quiet when clean.
"""
import pytest

from validator import validate_schedule
from helpers import make_division, make_config, get_check


CFG = make_config([make_division("D", [4])])  # teams: D A1..D A4


def game(t1, t2, court="Court 1", time="09:00", day=0, lbl="",
         loc="Blanes", div="D", group=""):
    return {"day": day, "time": time, "court": court, "loc": loc,
            "divName": div, "group": group, "t1": t1, "t2": t2, "lbl": lbl}


def test_clean_game_passes_the_core_checks():
    res = validate_schedule(CFG, [game("D A1", "D A2")])
    assert get_check(res, "valid court")["passed"]
    assert get_check(res, "court double-booked")["passed"]
    assert get_check(res, "team double-booked")["passed"]
    assert get_check(res, "plays itself")["passed"]


def test_self_play_is_caught():
    res = validate_schedule(CFG, [game("D A1", "D A1")])
    chk = get_check(res, "plays itself")
    assert not chk["passed"]
    assert chk["violations"]


def test_court_double_booking_is_caught():
    games = [
        game("D A1", "D A2", court="Court 1", time="09:00"),
        game("D A3", "D A4", court="Court 1", time="09:00"),
    ]
    chk = get_check(validate_schedule(CFG, games), "court double-booked")
    assert not chk["passed"]
    assert chk["violations"]


def test_team_double_booking_is_caught():
    # D A1 appears in two games at the same time on different courts.
    games = [
        game("D A1", "D A2", court="Court 1", time="09:00"),
        game("D A1", "D A3", court="Court 2", time="09:00"),
    ]
    chk = get_check(validate_schedule(CFG, games), "team double-booked")
    assert not chk["passed"]
    assert chk["violations"]


def test_unknown_court_is_flagged():
    games = [game("D A1", "D A2", court="Nonexistent Arena")]
    chk = get_check(validate_schedule(CFG, games), "valid court")
    assert not chk["passed"]


def test_max_games_per_day_is_caught():
    # maxGPD defaults to 2; give D A1 three games on day 0.
    games = [
        game("D A1", "D A2", court="Court 1", time="09:00"),
        game("D A1", "D A3", court="Court 1", time="10:30"),
        game("D A1", "D A4", court="Court 1", time="12:00"),
    ]
    chk = get_check(validate_schedule(CFG, games), "per team per day")
    assert not chk["passed"]
    assert chk["violations"]


def test_return_shape():
    res = validate_schedule(CFG, [game("D A1", "D A2")])
    assert set(res.keys()) >= {"valid", "checks", "notes"}
    assert isinstance(res["checks"], list)
    assert all({"rule", "passed", "violations"} <= set(c) for c in res["checks"])


# ── rest between a team's games (180 same venue / 270 across venues) ──────────

def test_rest_too_close_same_venue_is_caught():
    games = [
        game("D A1", "D A2", court="Court 1", time="09:00"),
        game("D A1", "D A3", court="Court 2", time="10:30"),  # +90 min
    ]
    chk = get_check(validate_schedule(CFG, games), "rest between games")
    assert not chk["passed"]


def test_rest_ok_same_venue_passes():
    games = [
        game("D A1", "D A2", court="Court 1", time="09:00"),
        game("D A1", "D A3", court="Court 1", time="12:00"),  # +180 min
    ]
    chk = get_check(validate_schedule(CFG, games), "rest between games")
    assert chk["passed"]


def test_rest_too_close_across_venues_is_caught():
    games = [
        game("D A1", "D A2", court="Court 1", time="09:00", loc="Blanes"),
        game("D A1", "D A3", court="Court 7", time="10:30", loc="Palafolls"),
    ]
    chk = get_check(validate_schedule(CFG, games), "rest between games")
    assert not chk["passed"]  # venue change needs 270, only 90 here


# ── every team plays >= 1 RR game at the main venue ──────────────────────────

def test_team_with_no_main_venue_rr_is_caught():
    # The only RR game is at Palafolls -> A1/A2 have no Blanes RR game.
    games = [game("D A1", "D A2", court="Court 7", loc="Palafolls")]
    chk = get_check(validate_schedule(CFG, games), "round-robin game at")
    assert not chk["passed"]


def test_main_venue_rr_present_passes():
    games = [game("D A1", "D A2", court="Court 1", loc="Blanes")]
    chk = get_check(validate_schedule(CFG, games), "round-robin game at")
    assert chk["passed"]


# ── division mandatory-venue rule ────────────────────────────────────────────

def _mandatory_blanes_cfg():
    return make_config(
        [make_division("D", [4])],
        venueRules=[{"divName": "D", "venues": ["Blanes"], "mode": "mandatory"}],
    )


def test_division_off_its_mandatory_venue_is_caught():
    cfg = _mandatory_blanes_cfg()
    games = [game("D A1", "D A2", court="Court 7", loc="Palafolls")]
    chk = get_check(validate_schedule(cfg, games), "mandatory-venue")
    assert not chk["passed"]


def test_division_on_its_mandatory_venue_passes():
    cfg = _mandatory_blanes_cfg()
    games = [game("D A1", "D A2", court="Court 1", loc="Blanes")]
    chk = get_check(validate_schedule(cfg, games), "mandatory-venue")
    assert chk["passed"]


# ── per-team venue block ─────────────────────────────────────────────────────

def _team_block_cfg():
    return make_config(
        [make_division("D", [4])],
        teamVenueRules=[{
            "teams": [{"div": "D", "team": "D A1"}],
            "venues": ["Palafolls"], "mode": "mandatory",
        }],
    )


def test_blocked_team_at_blocked_venue_is_caught():
    cfg = _team_block_cfg()
    games = [game("D A1", "D A2", court="Court 7", loc="Palafolls")]
    chk = get_check(validate_schedule(cfg, games), "venue-block")
    assert not chk["passed"]


def test_blocked_team_elsewhere_passes():
    cfg = _team_block_cfg()
    games = [game("D A1", "D A2", court="Court 1", loc="Blanes")]
    chk = get_check(validate_schedule(cfg, games), "venue-block")
    assert chk["passed"]


# ── team late-arrival ────────────────────────────────────────────────────────

def _late_arrival_cfg():
    return make_config(
        [make_division("D", [4])],
        teamAvailability=[{
            "teams": [{"div": "D", "team": "D A1"}],
            "day": 0, "notBefore": "12:00",
        }],
    )


def test_team_playing_before_arrival_is_caught():
    cfg = _late_arrival_cfg()
    games = [game("D A1", "D A2", time="09:00")]
    chk = get_check(validate_schedule(cfg, games), "late-arrival")
    assert not chk["passed"]


def test_team_playing_after_arrival_passes():
    cfg = _late_arrival_cfg()
    games = [game("D A1", "D A2", time="12:00")]
    chk = get_check(validate_schedule(cfg, games), "late-arrival")
    assert chk["passed"]


# ── venue blackout windows ───────────────────────────────────────────────────

def _blackout_cfg():
    return make_config(
        [make_division("D", [4])],
        venueBlackouts=[{"venue": "Palafolls", "day": 0,
                         "afterTime": "16:00", "beforeTime": ""}],
    )


def test_game_in_blacked_out_window_is_caught():
    cfg = _blackout_cfg()
    games = [game("D A1", "D A2", court="Court 7", time="16:00", loc="Palafolls")]
    chk = get_check(validate_schedule(cfg, games), "blackout")
    assert not chk["passed"]


def test_game_outside_blackout_passes():
    cfg = _blackout_cfg()
    games = [game("D A1", "D A2", court="Court 7", time="09:00", loc="Palafolls")]
    chk = get_check(validate_schedule(cfg, games), "blackout")
    assert chk["passed"]


# ── round-robin completeness (per group) ─────────────────────────────────────

def _all_rr_games():
    teams = ["D A1", "D A2", "D A3", "D A4"]
    pairs = [(a, b) for i, a in enumerate(teams) for b in teams[i + 1:]]
    # 6 distinct slot/court combos so nothing else trips.
    times = ["09:00", "10:30", "12:00", "14:30", "16:00", "17:30"]
    return [game(a, b, court="Court %d" % ((i % 6) + 1),
                 time=times[i], group="D Group A")
            for i, (a, b) in enumerate(pairs)]


def test_complete_round_robin_passes():
    chk = get_check(validate_schedule(CFG, _all_rr_games()), "round-robin games present")
    assert chk["passed"]


def test_missing_round_robin_game_is_caught():
    games = _all_rr_games()[:-1]  # drop one matchup
    chk = get_check(validate_schedule(CFG, games), "round-robin games present")
    assert not chk["passed"]


def test_duplicate_round_robin_game_is_caught():
    games = _all_rr_games()
    games.append(game("D A1", "D A2", court="Court 6", time="14:30", group="D Group A"))
    chk = get_check(validate_schedule(CFG, games), "round-robin games present")
    assert not chk["passed"]


# ── round-robin must precede playoffs (per division) ─────────────────────────

def test_rr_after_playoff_is_caught():
    games = [
        game("", "", court="Court 1", time="09:00", day=1, lbl="FINAL"),
        game("D A1", "D A2", court="Court 2", time="10:30", day=1, group="D Group A"),
    ]
    chk = get_check(validate_schedule(CFG, games), "before playoffs")
    assert not chk["passed"]


def test_rr_before_playoff_passes():
    games = [
        game("D A1", "D A2", court="Court 1", time="09:00", day=0, group="D Group A"),
        game("", "", court="Court 1", time="09:00", day=1, lbl="FINAL"),
    ]
    chk = get_check(validate_schedule(CFG, games), "before playoffs")
    assert chk["passed"]


# ── aggregate valid flag + roster-drift note ─────────────────────────────────

def test_valid_flag_is_false_when_a_rule_breaks():
    res = validate_schedule(CFG, [game("D A1", "D A1")])  # self-play
    assert res["valid"] is False


def test_renamed_team_produces_a_note():
    res = validate_schedule(CFG, [game("D RENAMED", "D A2", group="D Group A")])
    assert res["notes"]  # roster-drift note present
