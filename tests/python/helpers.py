"""Shared builders for the Python test suite.

Kept as a plain importable module (not conftest) so both the tests and the
conftest fixtures can use the same factories. `tests/python` is on sys.path in
pytest's prepend import mode, so `import helpers` resolves here; `pythonpath = .`
in pytest.ini puts the repo root on sys.path for `import scheduler` etc.
"""

DEFAULT_SITES = [
    {"name": "Blanes", "numCourts": 6},
    {"name": "Palafolls", "numCourts": 2},
]

DEFAULT_SETUP = {
    "nDays": 3,
    "maxGPD": 2,
    "mainVenue": "Blanes",
    "lS": "13:30",
    "lE": "14:30",
}


def make_division(name, group_sizes, color="#888888"):
    """Build a division dict with `manualGroups` of the given sizes.

    group_sizes=[4,3,3,3] -> group A has 4 teams, B/C/D have 3. Team names are
    unique and carry no country tag (the PO builder only looks at group sizes;
    tests that need specific names build divisions directly).
    """
    groups = []
    teams = []
    for gi, size in enumerate(group_sizes):
        letter = chr(65 + gi)
        grp = ["%s %s%d" % (name, letter, i + 1) for i in range(size)]
        groups.append(grp)
        teams.extend(grp)
    return {"name": name, "color": color, "teams": teams, "manualGroups": groups}


def make_config(divisions, sites=None, setup=None, **extra):
    """Assemble a config of the shape /api/generate receives.

    extra carries the optional top-level arrays the solver reads, e.g.
    teamZones=[...], zoneVenueRules=[...], requiredTrainingGames=[...],
    teamVenueRules=[...], venueBlackouts=[...], teamAvailability=[...].
    """
    cfg = {
        "divisions": divisions,
        "sites": sites if sites is not None else [dict(s) for s in DEFAULT_SITES],
        "setupFields": dict(DEFAULT_SETUP, **(setup or {})),
    }
    cfg.update(extra)
    return cfg


def get_check(result, rule_substr):
    """Return the first validator check whose rule name contains rule_substr."""
    for c in result.get("checks", []):
        if rule_substr.lower() in c["rule"].lower():
            return c
    raise AssertionError(
        "no validator check matching %r; have: %s"
        % (rule_substr, [c["rule"] for c in result.get("checks", [])])
    )


def iter_sched_games(sched):
    """Yield (game, div_name, day_index) for every placed game in a solver
    `sched`, handling both shapes (d.games and d.groups[gk].games)."""
    for di, day in enumerate(sched.get("gameDays", []) or []):
        for d in day.get("divs", []):
            if d.get("games"):
                for g in d["games"]:
                    yield g, d.get("name"), di
            for gk, gv in (d.get("groups") or {}).items():
                for g in gv.get("games", []):
                    yield g, d.get("name"), di


def flatten_sched(result):
    """Flatten a solve_schedule result into the flat game list validate_schedule
    consumes: {day, time, court, loc, divName, group, t1, t2, lbl}.

    Walks gameDays only — bracketDays reference the same playoff game objects
    already present in gameDays (under the "<div> — Playoffs" group), so
    including them would double-count.
    """
    sched = result["sched"]
    out = []
    for di, day in enumerate(sched.get("gameDays", []) or []):
        day_idx = day.get("dayIndex", di)
        for d in day.get("divs", []):
            dn = d.get("name")
            for g in (d.get("games") or []):
                out.append(_flat_game(g, dn, day_idx, ""))
            for gk, gv in (d.get("groups") or {}).items():
                for g in gv.get("games", []):
                    out.append(_flat_game(g, dn, day_idx, gk))
    return out


def _flat_game(g, div_name, day_idx, group):
    return {
        "day": day_idx,
        "time": g.get("time"),
        "court": g.get("court"),
        "loc": g.get("loc"),
        "divName": div_name,
        "group": group,
        "t1": g.get("t1"),
        "t2": g.get("t2"),
        "lbl": g.get("lbl", ""),
    }
