"""Parse an audit-prompt markdown file and run the No-Blank-Day measurement
on it (without re-running the solver).

Useful when you have an audit doc but no JSON snapshot — e.g. when verifying
a schedule that was generated and exported but the JSON wasn't saved.

Usage:
    python verify_audit_blank_days.py audit-2026-05-08.md
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict

PLACEHOLDER_RE = re.compile(r'^(?:1st|2nd|3rd|4th|5th|6th|7th|8th)\s+Group\s+([A-Z])$')


def parse_divisions(text: str) -> list[dict]:
    """Find the '## Divisions' section and parse each division's groups."""
    divisions = []
    in_section = False
    current_div = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith('## Divisions'):
            in_section = True
            continue
        if in_section and line.startswith('## ') and 'Divisions' not in line:
            break
        if not in_section:
            continue
        # Division header: "- **U12 MIXED** (12 teams)"
        m = re.match(r'^- \*\*([^*]+)\*\*\s*\((\d+) teams\)', line)
        if m:
            current_div = {'name': m.group(1).strip(), 'manualGroups': [], 'teams': []}
            divisions.append(current_div)
            continue
        # Group line: "  - Group A: TEAM1, TEAM2, TEAM3"
        m = re.match(r'^\s+- Group ([A-Z]):\s*(.+)$', line)
        if m and current_div is not None:
            teams = [t.strip() for t in m.group(2).split(',') if t.strip()]
            current_div['manualGroups'].append(teams)
            current_div['teams'].extend(teams)
    return divisions


def parse_schedule(text: str) -> list[dict]:
    """Find the '## Generated Schedule' table and yield game records."""
    games = []
    in_table = False
    saw_header = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('## Generated Schedule'):
            in_table = True
            continue
        if not in_table:
            continue
        if line.startswith('## ') and 'Generated Schedule' not in line:
            break
        if not line.startswith('|'):
            continue
        # Skip header and separator
        cols = [c.strip() for c in line.strip('|').split('|')]
        if not saw_header:
            if cols and cols[0] == 'Day':
                saw_header = True
            continue
        if all(c.startswith('-') or c == '' for c in cols):
            continue  # separator
        if len(cols) < 8:
            continue
        day_str, time_, venue, court, division, label, t1, t2 = cols[:8]
        # "Day 1" -> 0
        m = re.match(r'Day\s+(\d+)', day_str)
        if not m:
            continue
        d_idx = int(m.group(1)) - 1
        games.append({
            'day': d_idx,
            'division': division.strip(),
            't1': t1.strip(),
            't2': t2.strip(),
            'label': label.strip(),
        })
    return games


def measure_blanks(divisions: list[dict], games: list[dict], n_days: int = 3) -> dict:
    """Replicates measure_blank_days.measure() on parsed audit data.

    A team T in group X with positions 1..k is considered to play on day d
    iff any of these hold:
      (a) Concrete: a game on d names T as t1 or t2.
      (b) Position-covered: ALL k positions of group X are covered by
          placeholder games on day d (so regardless of which position T
          ends up at, T plays on day d).
      (c) Downstream TBD: a game on d in T's division has empty t1/t2
          (Final / 3rd Place / medal final waiting on SF results).

    The earlier version of this function (and measure_blank_days.py) had
    a false-negative bug where any concrete team's appearance credited
    the entire group as "playing", which masked real blank days in
    3-team groups. This corrected version tracks per-position coverage.
    """
    team_to_group: dict[tuple[str, str], str] = {}
    group_to_size: dict[tuple[str, str], int] = {}
    all_teams: list[tuple[str, str]] = []
    for div in divisions:
        for gi, grp in enumerate(div['manualGroups']):
            letter = chr(65 + gi)
            group_to_size[(div['name'], letter)] = len(grp)
            for t in grp:
                team_to_group[(div['name'], t)] = letter
                all_teams.append((div['name'], t))

    concrete: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0] * n_days)
    # (div, group_letter, day) -> set of position labels covered by
    # placeholder games on that day.
    position_coverage: dict[tuple[str, str, int], set] = defaultdict(set)
    div_tbd: dict[str, list[int]] = defaultdict(lambda: [0] * n_days)

    for g in games:
        d = g['day']
        div = g['division']
        t1, t2 = g['t1'], g['t2']
        is_tbd = (not t1 or t1 == '(TBD)') and (not t2 or t2 == '(TBD)')
        if is_tbd:
            div_tbd[div][d] += 1
            continue
        for raw in (t1, t2):
            if not raw or raw == '(TBD)':
                continue
            m = PLACEHOLDER_RE.match(raw)
            if m:
                position = raw.split(' Group ', 1)[0].strip()
                position_coverage[(div, m.group(1), d)].add(position)
            else:
                # Concrete team — credit only this specific team.
                if (div, raw) in team_to_group:
                    concrete[(div, raw)][d] += 1

    POSITION_LABELS = ['1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th']

    blank_pairs = []
    rows = []
    for div_name, team in all_teams:
        letter = team_to_group.get((div_name, team), '')
        group_size = group_to_size.get((div_name, letter), 1)
        required_positions = set(POSITION_LABELS[:group_size])
        counts = list(concrete.get((div_name, team), [0] * n_days))
        tbd = div_tbd.get(div_name, [0] * n_days)
        team_blanks = 0
        for d in range(n_days):
            if counts[d] > 0:
                continue
            if tbd[d] > 0:
                continue
            covered = position_coverage.get((div_name, letter, d), set())
            if required_positions.issubset(covered):
                continue
            blank_pairs.append((div_name, team, d))
            team_blanks += 1
        rows.append((div_name, team, counts, team_blanks))

    return {
        'rows': rows,
        'blank_pairs': blank_pairs,
        'total_blanks': len(blank_pairs),
        'teams_with_any_blank': sum(1 for r in rows if r[3] > 0),
        'n_teams': len(all_teams),
    }


def main(path: str) -> int:
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    divisions = parse_divisions(text)
    games = parse_schedule(text)
    print(f'Parsed {len(divisions)} divisions and {len(games)} games from {path}')
    print()

    n_days = max((g['day'] for g in games), default=2) + 1
    result = measure_blanks(divisions, games, n_days)

    print('=' * 72)
    print(f'BLANK DAY REPORT - {result["n_teams"]} teams across {n_days} days')
    print('=' * 72)
    print()
    print(f'Teams with >=1 blank day: {result["teams_with_any_blank"]} / {result["n_teams"]}')
    print(f'Total (team, day) blank pairs: {result["total_blanks"]}')
    if result['blank_pairs']:
        print()
        print('Blank pairs:')
        for div_name, team, d in result['blank_pairs']:
            print(f'  - {div_name} / {team} - Day {d + 1}')
    else:
        print()
        print('No blank days. Every team plays at least 1 game on every day.')
    print()
    return 0 if result['total_blanks'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'audit-2026-05-08.md'))
