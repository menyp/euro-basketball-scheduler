"""Standalone blank-day measurement helper.

Analyzes a generated schedule and reports, per team, how many games they have
on each day. Surfaces "blank days" (teams with zero games on a given day) so
we can verify the Daily Game Distribution rule does what it claims.

Usage:
    # Analyze a saved snapshot (cpsat_output.json or any export with `sched`)
    python measure_blank_days.py --snapshot cpsat_output.json

    # Generate fresh from a config and then analyze
    python measure_blank_days.py --config cpsat_output.json --regenerate

The script is intentionally pure-Python and dependency-light (only stdlib for
the snapshot path; the regenerate path imports `scheduler.solve_schedule`).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from typing import Iterator

# Matches placeholders like "1st Group A", "2nd Group D", "4th Group C".
PLACEHOLDER_RE = re.compile(
    r'^(?:1st|2nd|3rd|4th|5th|6th|7th|8th)\s+Group\s+([A-Z])$'
)


def iter_games(sched: dict) -> Iterator[tuple[int, str, str, str]]:
    """Yield (dayIndex, divName, t1, t2) for every game in a sched dict.

    Handles both shapes documented in CLAUDE.md:
      - d.groups[gk].games[]  (RR)
      - d.games[]             (bracket / consolation)

    Yields ALL games, including those with empty t1/t2 (downstream Finals
    waiting on SF results). The caller is responsible for interpreting them.
    """
    for d in sched.get('gameDays', []):
        d_idx = d.get('dayIndex')
        for div in d.get('divs', []):
            div_name = div.get('name', '')
            if div.get('groups'):
                for gk, gv in div['groups'].items():
                    for g in gv.get('games', []):
                        yield d_idx, div_name, g.get('t1', ''), g.get('t2', '')
            elif div.get('games'):
                for g in div['games']:
                    yield d_idx, div_name, g.get('t1', ''), g.get('t2', '')


def all_teams(divisions: list) -> list[tuple[str, str]]:
    """Return list of (divName, teamName) for every team in every division."""
    teams = []
    for div in divisions:
        div_name = div.get('name', '')
        for t in div.get('teams', []):
            teams.append((div_name, t))
    return teams


def build_group_index(divisions: list):
    """Return (team_to_group, group_to_teams) lookups."""
    team_to_group: dict[tuple[str, str], str] = {}
    group_to_teams: dict[tuple[str, str], set] = defaultdict(set)
    for div in divisions:
        div_name = div.get('name', '')
        mg = div.get('manualGroups') or []
        for gi, grp in enumerate(mg):
            letter = chr(65 + gi)
            for t in grp:
                team_to_group[(div_name, t)] = letter
                group_to_teams[(div_name, letter)].add(t)
    return team_to_group, group_to_teams


def measure(sched: dict, divisions: list, n_days: int) -> dict:
    """Build per-team-per-day game accounting and surface real blank days.

    A team T in group X with positions 1..k is considered to play on day d
    iff any of these hold:
      (a) Concrete: a game on day d names T as t1 or t2.
      (b) Position-covered: ALL k positions of group X are covered by
          placeholder games on day d (so regardless of which position T
          ends up at, T plays on day d).
      (c) Downstream TBD: a game on day d in the team's division has empty
          t1/t2 (Final / 3rd Place / medal final waiting on SF). The
          downstream game involves the SF winners/losers, which in turn
          come from games referencing T's group via SFs, so T is involved
          regardless of position. (Strictly safe assumption.)

    A "blank pair" is (team, day) where none of (a)/(b)/(c) hold.

    Note: an earlier version of this function used a per-group "active"
    flag set to True whenever any concrete team in the group played. That
    was a false-negative bug — it credited team T for playing whenever
    any other team in T's group played. The fixed version below tracks
    per-position coverage from placeholder references only, never crediting
    a team based on another team's concrete game.
    """
    team_to_group, group_to_teams = build_group_index(divisions)

    concrete_games: dict[tuple[str, str], list[int]] = defaultdict(
        lambda: [0] * n_days
    )
    # (div, group_letter, day) -> set of position labels covered by
    # placeholder games on that day. e.g. {'1st', '2nd', '3rd'} means
    # all three Group-X positions are covered.
    position_coverage: dict[tuple[str, str, int], set] = defaultdict(set)
    # (div) -> [int] per day: count of TBD games (downstream of SFs).
    tbd_games_per_div: dict[str, list[int]] = defaultdict(
        lambda: [0] * n_days
    )

    for d_idx, div_name, t1, t2 in iter_games(sched):
        if d_idx is None or d_idx >= n_days:
            continue
        # TBD downstream game (no teams resolved yet).
        if not t1 and not t2:
            tbd_games_per_div[div_name][d_idx] += 1
            continue
        for raw in (t1, t2):
            if not raw:
                continue
            m = PLACEHOLDER_RE.match(raw)
            if m:
                # Placeholder — record exactly which position is covered.
                position = raw.split(' Group ', 1)[0].strip()
                position_coverage[(div_name, m.group(1), d_idx)].add(position)
            else:
                # Concrete team — credit only this specific team.
                # (No more group-wide credit; that was the bug.)
                if (div_name, raw) in team_to_group:
                    concrete_games[(div_name, raw)][d_idx] += 1

    POSITION_LABELS = ['1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th']

    teams = all_teams(divisions)
    blank_pairs: list[tuple[str, str, int]] = []
    rows: list = []
    for div_name, team in teams:
        letter = team_to_group.get((div_name, team), '')
        group_size = len(group_to_teams.get((div_name, letter), {team}))
        required_positions = set(POSITION_LABELS[:group_size])
        counts = list(concrete_games.get((div_name, team), [0] * n_days))
        tbd = tbd_games_per_div.get(div_name, [0] * n_days)
        team_blanks = 0
        for d_idx in range(n_days):
            if counts[d_idx] > 0:
                continue  # team plays a concrete game this day
            if tbd[d_idx] > 0:
                continue  # downstream TBD covers all positions of all groups in div
            covered = position_coverage.get((div_name, letter, d_idx), set())
            if required_positions.issubset(covered):
                continue  # every position of team's group has a game on d
            blank_pairs.append((div_name, team, d_idx))
            team_blanks += 1
        rows.append((div_name, team, counts, team_blanks))

    return {
        'rows': rows,
        'blank_pairs': blank_pairs,
        'total_blanks': len(blank_pairs),
        'teams_with_any_blank': sum(1 for r in rows if r[3] > 0),
        'n_teams': len(teams),
    }


def print_report(result: dict, n_days: int, verbose: bool = False) -> None:
    print()
    print('=' * 72)
    print(f'BLANK DAY REPORT — {result["n_teams"]} teams across {n_days} days')
    print('=' * 72)

    if verbose:
        print()
        header = 'Division | Team' + ''.join(f' | D{i+1}' for i in range(n_days)) + ' | Blank'
        print(header)
        print('-' * len(header))
        for div_name, team, counts, blanks in result['rows']:
            counts_str = ' | '.join(str(c) for c in counts)
            print(f'{div_name:<14} | {team[:32]:<32} | {counts_str} | {blanks}')

    print()
    print(f'Teams with >=1 blank day: {result["teams_with_any_blank"]} / {result["n_teams"]}')
    print(f'Total (team, day) blank pairs: {result["total_blanks"]}')

    if result['blank_pairs']:
        print()
        print('Blank pairs:')
        for div_name, team, d_idx in result['blank_pairs']:
            print(f'  - {div_name} / {team} — Day {d_idx + 1}')
    else:
        print()
        print('No blank days. Every team plays at least 1 game on every day.')
    print()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument('--snapshot', help='Path to a JSON export with `sched` key')
    src.add_argument('--config', help='Path to a JSON config to regenerate from')
    p.add_argument('--regenerate', action='store_true',
                   help='With --config: call scheduler.solve_schedule and analyze fresh output')
    p.add_argument('--time-limit', type=int, default=120, help='Solver time limit (seconds)')
    p.add_argument('-v', '--verbose', action='store_true', help='Print per-team breakdown')
    args = p.parse_args()

    path = args.snapshot or args.config
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if args.snapshot:
        sched = data.get('sched', {})
        divisions = data.get('divisions', [])
        n_days = int(data.get('setupFields', {}).get('nDays') or len(sched.get('gameDays', [])) or 3)
    else:
        # --config (re-run solver)
        from scheduler import solve_schedule
        print(f'Regenerating schedule from {path} (time_limit={args.time_limit}s)...')
        result = solve_schedule(data, time_limit=args.time_limit)
        if 'error' in result:
            print(f'ERROR: solver returned: {result["error"]}', file=sys.stderr)
            if result.get('reason') == 'no_blank_day_mandatory_infeasible':
                print(file=sys.stderr)
                print('Blocked team-days:', file=sys.stderr)
                for v in result.get('blocked_team_days', []):
                    print(f'  - {v["divName"]} / {v["team"]} (Day {v["day"] + 1})', file=sys.stderr)
            return 2
        sched = result.get('sched', {})
        divisions = result.get('divisions', data.get('divisions', []))
        n_days = int(data.get('setupFields', {}).get('nDays') or 3)
        warnings = result.get('noBlankDayWarnings', [])
        if warnings:
            print(f'No-Blank-Day soft warnings ({len(warnings)} unavoidable blanks):')
            for v in warnings:
                print(f'  - {v["divName"]} / {v["team"]} (Day {v["day"] + 1})')

    result = measure(sched, divisions, n_days)
    print_report(result, n_days, verbose=args.verbose)
    return 0 if result['total_blanks'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
