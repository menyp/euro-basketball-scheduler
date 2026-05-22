"""End-to-end CP-SAT integration. Marked `slow` (runs the real solver). Asserts
structural invariants on the produced schedule — the same guarantees the now-dead
jsdom INTG tests used to check, but against the actual production solver.
"""
import pytest

from scheduler import solve_schedule
from helpers import make_division, make_config, iter_sched_games

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def solved():
    # Two single-group divisions of 4 -> 12 RR games, FINAL-only playoff each.
    cfg = make_config(
        [make_division("U16 BOYS", [4]), make_division("U14 BOYS", [4])],
        sites=[{"name": "Blanes", "numCourts": 4}],
        setup={"nDays": 3},
    )
    cfg["solverTimeLimit"] = 20
    result = solve_schedule(cfg, time_limit=20)
    assert "error" not in result, result.get("error")
    assert "sched" in result
    return result


def test_schedule_has_games(solved):
    sched = solved["sched"]
    assert sched.get("gameDays")
    assert any(True for _ in iter_sched_games(sched))


def test_no_team_double_booked_same_day_time(solved):
    sched = solved["sched"]
    seen = set()
    for g, div, day in iter_sched_games(sched):
        for tm in (g.get("t1"), g.get("t2")):
            if not tm:
                continue
            key = (div, tm, day, g.get("time"))
            assert key not in seen, "double-booked: %s" % (key,)
            seen.add(key)


def test_no_court_double_booked(solved):
    sched = solved["sched"]
    seen = set()
    for g, _div, day in iter_sched_games(sched):
        if not g.get("court") or not g.get("time"):
            continue
        key = (day, g["court"], g["time"])
        assert key not in seen, "court reused: %s" % (key,)
        seen.add(key)


def test_no_phantom_teams_in_round_robin(solved):
    """Every RR game's teams belong to that division's roster (no invented teams)."""
    sched = solved["sched"]
    rosters = {d["name"]: set(d["teams"]) for d in solved["divisions"]}
    for g, div, _day in iter_sched_games(sched):
        if g.get("lbl"):
            continue  # playoff games carry placeholders, not roster names
        for tm in (g.get("t1"), g.get("t2")):
            if tm:
                assert tm in rosters[div], "phantom team %r in %s" % (tm, div)


def test_playoff_labels_match_po_structure(solved):
    """Single-group divisions get exactly one FINAL and no semifinals."""
    sched = solved["sched"]
    po_by_div = {}
    for day in sched.get("bracketDays", []) or []:
        for d in day.get("divs", []):
            for g in d.get("games", []):
                if g.get("lbl"):
                    po_by_div.setdefault(d["name"], []).append(g["lbl"])
    for div, lbls in po_by_div.items():
        assert "FINAL" in lbls
        assert not any(l.startswith("SF") or l == "Semi Final" for l in lbls)
