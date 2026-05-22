"""Solver-output invariants: run the real CP-SAT solver and assert its OUTPUT
honors the hard rules it was given. Marked slow (stochastic + seconds). Assert
invariants, not exact placements.
"""
import pytest

from scheduler import solve_schedule
from validator import validate_schedule
from helpers import make_division, make_config, iter_sched_games, flatten_sched, get_check

pytestmark = pytest.mark.slow


def _solve(cfg, time_limit=20):
    cfg = dict(cfg, solverTimeLimit=time_limit)
    result = solve_schedule(cfg, time_limit=time_limit)
    assert "error" not in result, result.get("error")
    return result


def test_mandatory_division_venue_is_honored():
    cfg = make_config(
        [make_division("U18 BOYS", [4]), make_division("U14 BOYS", [4])],
        sites=[{"name": "Blanes", "numCourts": 4}, {"name": "Palafolls", "numCourts": 2}],
        setup={"nDays": 3},
        venueRules=[{"divName": "U18 BOYS", "venues": ["Blanes"], "mode": "mandatory"}],
    )
    result = _solve(cfg)
    off_venue = [
        (g.get("time"), g.get("loc"))
        for g, div, _day in iter_sched_games(result["sched"])
        if div == "U18 BOYS" and not g.get("lbl")  # RR games
        and "blanes" not in (g.get("loc") or "").lower()
    ]
    assert off_venue == [], "U18 BOYS RR games off Blanes: %s" % off_venue


def test_max_gpd_is_honored_in_output():
    cfg = make_config(
        [make_division("U16 BOYS", [4]), make_division("U14 BOYS", [4])],
        sites=[{"name": "Blanes", "numCourts": 4}],
        setup={"nDays": 3, "maxGPD": 2},
    )
    result = _solve(cfg)
    counts = {}
    for g, div, day in iter_sched_games(result["sched"]):
        for tm in (g.get("t1"), g.get("t2")):
            if not tm or tm == "TBD":
                continue
            counts[(div, tm, day)] = counts.get((div, tm, day), 0) + 1
    over = {k: n for k, n in counts.items() if n > 2}
    assert over == {}, "teams exceeding maxGPD: %s" % over


def test_solver_runs_in_seat_mode_with_pax():
    """With per-team PAX + zones provided, the solver takes the seat-capacity
    shuttle path; assert it still produces a valid, double-booking-free schedule."""
    divs = [make_division("U16 BOYS", [4]), make_division("U14 BOYS", [4])]
    team_zones, team_pax = [], []
    for d in divs:
        for i, t in enumerate(d["teams"]):
            team_zones.append({"div": d["name"], "team": t,
                               "zone": "white" if i % 2 == 0 else "black"})
            team_pax.append({"div": d["name"], "team": t, "pax": 20 + i})
    cfg = make_config(
        divs,
        sites=[{"name": "Blanes", "numCourts": 4}, {"name": "Palafolls", "numCourts": 2}],
        setup={"nDays": 3},
        teamZones=team_zones,
        teamPax=team_pax,
    )
    result = _solve(cfg)
    seen = set()
    for g, div, day in iter_sched_games(result["sched"]):
        for tm in (g.get("t1"), g.get("t2")):
            if not tm:
                continue
            key = (div, tm, day, g.get("time"))
            assert key not in seen, "double-booked under seat mode: %s" % (key,)
            seen.add(key)


def test_solver_output_passes_its_own_validator():
    """The solver's RR/PO placements, judged by the same rulebook the validator
    enforces, report no hard-rule violations on the core checks."""
    cfg = make_config(
        [make_division("U16 BOYS", [4]), make_division("U14 BOYS", [4])],
        sites=[{"name": "Blanes", "numCourts": 4}],
        setup={"nDays": 3},
    )
    result = _solve(cfg)
    res = validate_schedule(cfg, flatten_sched(result))
    for rule in ("court double-booked", "team double-booked",
                 "plays itself", "rest between games"):
        chk = get_check(res, rule)
        assert chk["passed"], "%s -> %s" % (rule, chk["violations"][:3])
