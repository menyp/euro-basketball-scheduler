"""
EYBC Tournament Scheduler — Schedule Validator

The scheduler's second job: instead of *building* a schedule, check a
manually-edited one against the original setup.

It reuses the solver's own config parser (``_build_context``) so the rules can
never drift from what the solver enforces. It does NOT re-solve — every hard
rule is checked directly against the finished schedule, which yields precise,
human-readable violation messages.

Entry point::

    validate_schedule(config, games) -> dict

  config : the same config object ``/api/generate`` receives
           (divisions, sites, setupFields, venueRules, teamVenueRules,
            venueBlackouts, teamAvailability)
  games  : flat list of placed games, each a dict with keys
           {day, time, court, loc, divName, group, t1, t2, lbl}
  returns: {
             valid:  bool,
             checks: [{rule: str, passed: bool, violations: [str]}],
             notes:  [str]
           }

Round-robin games have an empty ``lbl``; playoff games have a non-empty one.
Manual edits are round-robin only, so most checks focus on RR games; checks
that span courts/slots/teams include playoff games too so an RR edit that
collides with a playoff game is still caught.
"""
from itertools import combinations
from collections import defaultdict

from scheduler import _build_context, _time_to_min, _min_to_time, _is_blacked_out


def _norm(s):
    return (s or '').strip()


def _group_letter(group_str):
    """'U18 BOYS Group A' -> 'A'. Returns '' if no letter is found."""
    g = group_str or ''
    idx = g.rfind('Group ')
    if idx != -1 and idx + 6 < len(g):
        return g[idx + 6].upper()
    return ''


def validate_schedule(config, games):
    ctx = _build_context(config)
    courts = ctx['courts']
    day_slots = ctx['day_slots']
    n_days = ctx['n_days']
    max_gpd = ctx['max_gpd']
    blanes_courts = set(ctx['blanes_courts'])
    div_allowed_courts = ctx['div_allowed_courts']
    team_blocked_courts = ctx['team_blocked_courts']
    team_arrival_min = ctx['team_arrival_min']
    rule_rest = ctx['rule_rest']
    rule_venue_rest = ctx['rule_venue_rest']
    groups = ctx['groups']
    lunch_start, lunch_end = ctx['lunch_start'], ctx['lunch_end']
    main_venue = ctx['main_venue']

    court_idx = {c['name']: i for i, c in enumerate(courts)}

    # Normalize every submitted game into a working record.
    recs = []
    for g in games:
        court_name = _norm(g.get('court'))
        ci = court_idx.get(court_name)
        t = g.get('time')
        rec = {
            'day': g.get('day', 0),
            'time': _norm(t),
            'min': _time_to_min(t) if t else None,
            'court': court_name,
            'court_idx': ci,
            'venue': courts[ci]['venue'] if ci is not None else _norm(g.get('loc')),
            'div': _norm(g.get('divName')),
            'group': _norm(g.get('group')),
            't1': _norm(g.get('t1')),
            't2': _norm(g.get('t2')),
            'lbl': _norm(g.get('lbl')),
        }
        rec['is_rr'] = not rec['lbl']
        rec['label'] = '%s vs %s [%s]' % (rec['t1'] or '?', rec['t2'] or '?', rec['div'])
        recs.append(rec)

    rr = [r for r in recs if r['is_rr']]

    checks = []
    notes = []

    def add(rule, violations):
        checks.append({'rule': rule, 'passed': len(violations) == 0,
                       'violations': violations})

    # ── valid court + valid start time (lunch is excluded from day_slots) ────
    v = []
    for r in recs:
        if r['court_idx'] is None:
            v.append('%s on unknown court "%s"' % (r['label'], r['court']))
            continue
        d = r['day']
        if not isinstance(d, int) or d < 0 or d >= n_days:
            v.append('%s on a day outside the tournament (day %s)' % (r['label'], d))
            continue
        if r['min'] is None or r['min'] not in day_slots[d]:
            if r['min'] is not None and lunch_start <= r['min'] < lunch_end:
                v.append('%s starts %s — inside the lunch break' % (r['label'], r['time']))
            else:
                v.append('%s at "%s" — not a valid start time on day %d'
                         % (r['label'], r['time'], d + 1))
    add('Every game on a valid court and start time', v)

    # ── court capacity: no two games share a court in the same slot ─────────
    v = []
    seen = {}
    for r in recs:
        if r['court_idx'] is None or r['min'] is None:
            continue
        k = (r['day'], r['court'], r['min'])
        if k in seen:
            v.append('%s and %s both on %s at %s (day %d)'
                     % (seen[k], r['label'], r['court'], r['time'], r['day'] + 1))
        else:
            seen[k] = r['label']
    add('No court double-booked', v)

    # ── no team in two games at the same time ───────────────────────────────
    v = []
    seen = {}
    for r in recs:
        if r['min'] is None:
            continue
        for tm in (r['t1'], r['t2']):
            if not tm:
                continue
            k = (r['day'], r['div'], tm.lower(), r['min'])
            if k in seen:
                v.append('%s [%s] is in two games at %s on day %d'
                         % (tm, r['div'], r['time'], r['day'] + 1))
            else:
                seen[k] = True
    add('No team double-booked', v)

    # ── no team plays itself ────────────────────────────────────────────────
    v = []
    for r in recs:
        if r['t1'] and r['t2'] and r['t1'].lower() == r['t2'].lower():
            v.append('%s plays itself (%s, day %d)' % (r['t1'], r['time'], r['day'] + 1))
    add('No team plays itself', v)

    # ── max games per team per day ──────────────────────────────────────────
    v = []
    cnt = defaultdict(int)
    for r in recs:
        for tm in (r['t1'], r['t2']):
            if tm:
                cnt[(r['div'], tm.lower(), r['day'])] += 1
    for (div, tm, day), n in sorted(cnt.items()):
        if n > max_gpd:
            v.append('%s [%s] has %d games on day %d (max %d)'
                     % (tm, div, n, day + 1, max_gpd))
    add('Max %d games per team per day' % max_gpd, v)

    # ── rest between a team's games on the same day ─────────────────────────
    v = []
    by_team_day = defaultdict(list)
    for r in recs:
        if r['min'] is None:
            continue
        for tm in (r['t1'], r['t2']):
            if tm:
                by_team_day[(r['div'], tm.lower(), r['day'])].append(r)
    for (div, tm, day), rs in by_team_day.items():
        rs2 = sorted(rs, key=lambda r: r['min'])
        for a, b in combinations(rs2, 2):
            gap = abs(b['min'] - a['min'])
            same_v = a['venue'] == b['venue']
            if rule_rest and same_v and gap < 180:
                v.append('%s [%s] day %d: only %d min between %s and %s (same venue, need 180)'
                         % (tm, div, day + 1, gap, a['time'], b['time']))
            elif rule_venue_rest and not same_v and gap < 270:
                v.append('%s [%s] day %d: only %d min between %s (%s) and %s (%s) '
                         '— venue change needs 270'
                         % (tm, div, day + 1, gap, a['time'], a['venue'],
                            b['time'], b['venue']))
    add('Rest between games (180 min same venue / 270 min across venues)', v)

    # ── every team plays >= 1 round-robin game at the main venue ────────────
    v = []
    rr_teams = set()
    rr_team_at_main = set()
    for r in rr:
        for tm in (r['t1'], r['t2']):
            if not tm:
                continue
            key = (r['div'], tm.lower())
            rr_teams.add(key)
            if r['court_idx'] is not None and r['court_idx'] in blanes_courts:
                rr_team_at_main.add(key)
    for key in sorted(rr_teams):
        if key not in rr_team_at_main:
            v.append('%s [%s] has no round-robin game at %s' % (key[1], key[0], main_venue))
    add('Every team plays >=1 round-robin game at %s' % main_venue, v)

    # ── division Mandatory-venue rules ──────────────────────────────────────
    v = []
    for r in recs:
        allowed = div_allowed_courts.get(r['div'])
        if allowed and r['court_idx'] is not None and r['court_idx'] not in allowed:
            v.append('%s at %s — this division is restricted to other venues'
                     % (r['label'], r['venue']))
    add('Division mandatory-venue rules respected', v)

    # ── per-team venue blocks (round-robin only) ────────────────────────────
    v = []
    for r in rr:
        if r['court_idx'] is None:
            continue
        for tm in (r['t1'], r['t2']):
            blocked = team_blocked_courts.get((r['div'], tm))
            if blocked and r['court_idx'] in blocked:
                v.append('%s [%s] plays at blocked venue %s (%s, day %d)'
                         % (tm, r['div'], r['venue'], r['time'], r['day'] + 1))
    add('Team venue-block rules respected', v)

    # ── team late-arrival rules ─────────────────────────────────────────────
    v = []
    for r in recs:
        if r['min'] is None:
            continue
        for tm in (r['t1'], r['t2']):
            cutoff = team_arrival_min.get((r['div'], tm, r['day']))
            if cutoff and r['min'] < cutoff:
                v.append('%s [%s] plays %s on day %d but is not available before %s'
                         % (tm, r['div'], r['time'], r['day'] + 1, _min_to_time(cutoff)))
    add('Team late-arrival rules respected', v)

    # ── venue blackout windows ──────────────────────────────────────────────
    v = []
    for r in recs:
        if r['court_idx'] is None or r['min'] is None:
            continue
        if not isinstance(r['day'], int) or r['day'] < 0 or r['day'] >= n_days:
            continue
        if _is_blacked_out(ctx, r['court_idx'], r['day'], r['min']):
            v.append('%s uses %s at %s on day %d — venue is blacked out'
                     % (r['label'], r['venue'], r['time'], r['day'] + 1))
    add('Venue blackout windows respected', v)

    # ── round-robin completeness (count-based; survives renamed teams) ──────
    v = []
    expected = {}
    for (div, letter), teams in groups.items():
        n = len(teams)
        expected[(div, letter)] = n * (n - 1) // 2
    got = defaultdict(int)
    pair_seen = defaultdict(set)
    for r in rr:
        letter = _group_letter(r['group'])
        key = (r['div'], letter)
        got[key] += 1
        pair = tuple(sorted([r['t1'].lower(), r['t2'].lower()]))
        if pair in pair_seen[key]:
            v.append('%s Group %s: "%s vs %s" appears more than once'
                     % (r['div'], letter, r['t1'], r['t2']))
        pair_seen[key].add(pair)
    for key, exp in sorted(expected.items()):
        if got.get(key, 0) != exp:
            v.append('%s Group %s: %d round-robin games, expected %d'
                     % (key[0], key[1], got.get(key, 0), exp))
    add('All round-robin games present (per group)', v)

    # ── round-robin before playoffs (per division) ─────────────────────────
    v = []
    earliest_po = {}
    for r in recs:
        if r['is_rr'] or r['min'] is None:
            continue
        key = (r['day'], r['min'])
        cur = earliest_po.get(r['div'])
        if cur is None or key < cur:
            earliest_po[r['div']] = key
    for r in rr:
        if r['min'] is None:
            continue
        po = earliest_po.get(r['div'])
        if po and (r['day'], r['min']) >= po:
            v.append('%s round-robin game on day %d at %s runs at/after a playoff game'
                     % (r['label'], r['day'] + 1, r['time']))
    add('All round-robin games before playoffs (per division)', v)

    # ── note: teams that no longer match the original roster ────────────────
    known = set(ctx['all_teams'])
    unknown_teams = set()
    for r in rr:
        for tm in (r['t1'], r['t2']):
            if tm and (r['div'], tm) not in known:
                unknown_teams.add((r['div'], tm))
    if unknown_teams:
        notes.append('%d team name(s) differ from the original roster — '
                     'late-arrival and team-venue-block rules were only checked '
                     'for teams that still match by name.' % len(unknown_teams))

    return {
        'valid': all(c['passed'] for c in checks),
        'checks': checks,
        'notes': notes,
    }
