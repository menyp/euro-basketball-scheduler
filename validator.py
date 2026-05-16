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


def _build_recs(games, ctx):
    """Normalize a flat game list into working records."""
    courts = ctx['courts']
    court_idx = {c['name']: i for i, c in enumerate(courts)}
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
    return recs


def validate_schedule(config, games, original_games=None):
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

    recs = _build_recs(games, ctx)
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

    result = {
        'valid': all(c['passed'] for c in checks),
        'checks': checks,
        'notes': notes,
    }
    if original_games is not None:
        result['health'] = compare_health(ctx, _build_recs(original_games, ctx), recs)
    return result


# ── Health comparison (soft preferences) ────────────────────────────────────
# After the hard-rule pass/fail, compare the edited schedule to the original
# solver schedule on every soft preference the solver optimizes. The original
# is the 100% baseline; each rule shows its number plus, when it got worse,
# the specific games responsible (before -> after). Penalties use the solver's
# own coefficients (scheduler.py) so the headline % means what the solver means.

_REST_TARGET_GAP = 240          # scheduler.py REST_TARGET_GAP
_SLOT_TAIL_WEIGHT = 1           # scheduler.py SLOT_TAIL_WEIGHT
_BTB_WEIGHT = 4                 # scheduler.py BTB_WEIGHT
_OLDER_DIVS = {'U16 BOYS', 'U16 GIRLS', 'U18 BOYS', 'U18 GIRLS'}

# Relative importance of each soft rule in the headline %, mirroring how much
# the solver leans on it (venue + rest weigh most, then back-to-back and the
# finals showcase, then the gentle early-slot nudge). Each rule's raw penalties
# live on wildly different scales, so they can't just be summed — instead each
# rule gets a 0..1 "retention" score (below) and these weights blend them.
_HEALTH_WEIGHTS = {
    'Blanes saturation': 3,
    'Blanes priority by age/gender': 3,
    'Rest fairness': 3,
    'Back-to-back older divisions': 2,
    'Early-slot preference': 1,
    'Finals-day atmosphere': 2,
}


def _retention(p_orig, p_edit):
    """How much of a rule's quality the edit kept, 0..1 (1.0 = held or better).
    Smoothed so a small slip is a small drop, not a cliff."""
    if p_edit <= p_orig:
        return 1.0
    if p_orig > 0:
        # 2*orig / (orig + edit): edit == orig -> 1.0, edit == 2*orig -> 0.67.
        return min(1.0, 2.0 * p_orig / (p_orig + p_edit))
    # Original was clean on this rule; the edit introduced a problem.
    return max(0.0, 1.0 - p_edit / (p_edit + 10.0))


def _match_key(r):
    """Identity of an RR game — stable across time/court/day changes."""
    return (r['div'], _group_letter(r['group']),
            tuple(sorted([r['t1'].lower(), r['t2'].lower()])))


def _slot_index(ctx, day, minute):
    """Position of a slot start within its day (0-based), or None."""
    if not isinstance(day, int) or day < 0 or day >= len(ctx['day_slots']):
        return None
    slots = ctx['day_slots'][day]
    return slots.index(minute) if minute in slots else None


def _team_day_slots(rr_recs):
    """(div, team_lower, day) -> sorted list of slot-start minutes."""
    by = defaultdict(list)
    for r in rr_recs:
        if r['min'] is None:
            continue
        for tm in (r['t1'], r['t2']):
            if tm:
                by[(r['div'], tm.lower(), r['day'])].append(r['min'])
    for k in by:
        by[k].sort()
    return by


def _rest_penalty_and_min_gap(slots):
    """Squared-deficit rest penalty (solver formula) + tightest gap, for one
    team-day's sorted slot minutes."""
    penalty, min_gap = 0, None
    for i in range(len(slots)):
        for j in range(i + 1, len(slots)):
            gap = slots[j] - slots[i]
            if min_gap is None or gap < min_gap:
                min_gap = gap
            if gap < _REST_TARGET_GAP:
                penalty += (_REST_TARGET_GAP - gap) ** 2
    return penalty, min_gap


def _btb_pairs(ctx, rr_recs):
    """Set of (court_idx, day, slot_idx) where an older-division game sits
    directly before another older-division game on the same court (true
    90-min back-to-back, not separated by lunch)."""
    older_at = defaultdict(set)  # (court_idx, day) -> set of slot minutes
    for r in rr_recs:
        if r['div'] in _OLDER_DIVS and r['court_idx'] is not None and r['min'] is not None:
            older_at[(r['court_idx'], r['day'])].add(r['min'])
    pairs = set()
    for (ci, day), mins in older_at.items():
        if not isinstance(day, int) or day < 0 or day >= len(ctx['day_slots']):
            continue
        slots = ctx['day_slots'][day]
        for s in range(len(slots) - 1):
            if slots[s + 1] - slots[s] > 90:
                continue  # lunch-separated — solver doesn't penalise
            if slots[s] in mins and slots[s + 1] in mins:
                pairs.add((ci, day, s))
    return pairs


def compare_health(ctx, orig_recs, edit_recs):
    orig_rr = [r for r in orig_recs if r['is_rr']]
    edit_rr = [r for r in edit_recs if r['is_rr']]
    blanes = set(ctx['blanes_courts'])
    div_pref = ctx['div_preferred_courts']
    div_prio = ctx['div_priority']
    main_venue = ctx['main_venue']

    # Match edited RR games back to their originals by identity.
    orig_by_key = {_match_key(r): r for r in orig_rr}
    matched = []   # (orig, edited) pairs
    unmatched = 0
    for er in edit_rr:
        orr = orig_by_key.get(_match_key(er))
        if orr:
            matched.append((orr, er))
        else:
            unmatched += 1

    def preferred_set(div):
        return div_pref.get(div) or blanes

    def off_pref(r):
        return r['court_idx'] is not None and r['court_idx'] not in preferred_set(r['div'])

    def on_main(r):
        return r['court_idx'] is not None and r['court_idx'] in blanes

    metrics = []

    def emit(rule, p_orig, p_edit, disp_orig, disp_edit, culprits):
        if p_edit > p_orig:
            direction = 'worse'
        elif p_edit < p_orig:
            direction = 'better'
        else:
            direction = 'same'
        metrics.append({
            'rule': rule, 'original': disp_orig, 'edited': disp_edit,
            'direction': direction, 'culprits': culprits if direction == 'worse' else [],
            'penalty_orig': p_orig, 'penalty_edit': p_edit,
            'weight': _HEALTH_WEIGHTS.get(rule, 1),
        })

    # 1. Blanes saturation — % of RR games on the main venue (unweighted view).
    def sat(rr):
        return (sum(1 for r in rr if on_main(r)) / len(rr) * 100.0) if rr else 100.0
    sat_o, sat_e = sat(orig_rr), sat(edit_rr)
    sat_culprits = []
    for orr, er in matched:
        if on_main(orr) and not on_main(er):
            sat_culprits.append('%s vs %s [%s] — %s -> %s (left %s)'
                                % (er['t1'], er['t2'], er['div'], orr['court'],
                                   er['court'], main_venue))
    # penalty for the headline = number of RR games off the main venue
    emit('Blanes saturation',
         sum(1 for r in orig_rr if not on_main(r)),
         sum(1 for r in edit_rr if not on_main(r)),
         '%.0f%%' % sat_o, '%.0f%%' % sat_e, sat_culprits)

    # 2. Blanes priority by age/gender — off-preferred games weighted by
    #    division priority (older/boys weigh more). Solver's venue term.
    def prio_pen(rr):
        return sum(div_prio.get(r['div'], 1) for r in rr if off_pref(r))
    pp_o, pp_e = prio_pen(orig_rr), prio_pen(edit_rr)
    prio_culprits = []
    for orr, er in matched:
        if not off_pref(orr) and off_pref(er):
            w = div_prio.get(er['div'], 1)
            prio_culprits.append('%s vs %s [%s, priority %d] — %s -> %s'
                                 % (er['t1'], er['t2'], er['div'], w,
                                    orr['court'], er['court']))
    emit('Blanes priority by age/gender', pp_o, pp_e, str(pp_o), str(pp_e), prio_culprits)

    # 3. Rest fairness — squared-deficit penalty below a comfortable gap.
    tds_o = _team_day_slots(orig_rr)
    tds_e = _team_day_slots(edit_rr)
    rest_o = sum(_rest_penalty_and_min_gap(s)[0] for s in tds_o.values())
    rest_e = sum(_rest_penalty_and_min_gap(s)[0] for s in tds_e.values())
    rest_culprits = []
    for key, eslots in tds_e.items():
        oslots = tds_o.get(key)
        if oslots is None:
            continue  # renamed team — can't compare
        ep, emg = _rest_penalty_and_min_gap(eslots)
        op, omg = _rest_penalty_and_min_gap(oslots)
        if ep > op:
            div, team, day = key
            rest_culprits.append('%s [%s], Day %d — tightest gap %s min -> %s min'
                                 % (team, div, day + 1,
                                    omg if omg is not None else '-',
                                    emg if emg is not None else '-'))
    emit('Rest fairness', rest_o, rest_e,
         '%d tight-gap points' % rest_o, '%d tight-gap points' % rest_e, rest_culprits)

    # 4. Early-slot preference — how far into the day RR games sit.
    def tail_pen(rr):
        tot = 0
        for r in rr:
            si = _slot_index(ctx, r['day'], r['min'])
            if si:
                tot += _SLOT_TAIL_WEIGHT * si
        return tot
    tail_o, tail_e = tail_pen(orig_rr), tail_pen(edit_rr)
    tail_culprits = []
    for orr, er in matched:
        oi = _slot_index(ctx, orr['day'], orr['min'])
        ei = _slot_index(ctx, er['day'], er['min'])
        if oi is not None and ei is not None and ei > oi:
            tail_culprits.append('%s vs %s [%s] — Day %d %s -> Day %d %s'
                                 % (er['t1'], er['t2'], er['div'],
                                    orr['day'] + 1, orr['time'],
                                    er['day'] + 1, er['time']))
    emit('Early-slot preference', tail_o, tail_e, str(tail_o), str(tail_e), tail_culprits)

    # 5. Back-to-back older divisions — U16/U18 consecutive same-court.
    btb_o = _btb_pairs(ctx, orig_rr)
    btb_e = _btb_pairs(ctx, edit_rr)
    btb_culprits = []
    for (ci, day, s) in sorted(btb_e - btb_o):
        court_name = ctx['courts'][ci]['name'] if ci < len(ctx['courts']) else '?'
        t1 = _min_to_time(ctx['day_slots'][day][s])
        t2 = _min_to_time(ctx['day_slots'][day][s + 1])
        btb_culprits.append('%s, Day %d — new back-to-back at %s -> %s'
                            % (court_name, day + 1, t1, t2))
    emit('Back-to-back older divisions', len(btb_o), len(btb_e),
         '%d pairs' % len(btb_o), '%d pairs' % len(btb_e), btb_culprits)

    # 6. Finals-day atmosphere — Finals/3rd distance from prime slot. Manual
    #    edits are RR-only, so playoffs (and this metric) never move.
    def finals_pen(recs):
        tot = 0
        for r in recs:
            lbl = r['lbl'].upper()
            if r['min'] is None:
                continue
            if lbl.startswith('FINAL'):
                tot += abs(r['min'] - ctx['final_target'])
            elif '3RD' in lbl:
                tot += abs(r['min'] - ctx['third_target'])
        return tot
    fa_o, fa_e = finals_pen(orig_recs), finals_pen(edit_recs)
    emit('Finals-day atmosphere', fa_o, fa_e, str(fa_o), str(fa_e), [])

    # Headline: weighted blend of each rule's retention score. Raw penalties
    # can't be summed directly (rest-fairness's squared values would drown
    # everything), so each rule contributes a 0..1 score scaled by its weight.
    total_w = sum(m['weight'] for m in metrics)
    score = sum(m['weight'] * _retention(m['penalty_orig'], m['penalty_edit'])
                for m in metrics)
    pct = round(score / total_w * 100) if total_w else 100
    improved = (any(m['direction'] == 'better' for m in metrics)
                and not any(m['direction'] == 'worse' for m in metrics))

    note = ''
    if unmatched:
        note = ('%d edited game(s) could not be matched to the original '
                '(renamed teams) — their before/after detail is omitted.' % unmatched)

    return {
        'pct': min(pct, 100),
        'improved': improved,
        'metrics': metrics,
        'note': note,
    }


# ── Standalone per-division health report ───────────────────────────────────
# After generation the admin wants a deeper view than the hard-rule pass/fail
# — per-division Blanes saturation, per-team rest gaps, the actual back-to-back
# pairs, finals slot vs target, etc. report_health(config, games) produces
# that absolute per-division breakdown (no original-vs-edited comparison). It
# powers the Health section appended to the Audit Schedule modal.


def _hr_blanes_saturation(ctx, recs):
    blanes = set(ctx['blanes_courts'])
    div_prio = ctx['div_priority']
    # Priority rank: 1 = highest-priority division (largest div_priority weight).
    sorted_divs = sorted(div_prio.items(), key=lambda kv: -kv[1])
    rank_of = {d: i + 1 for i, (d, _) in enumerate(sorted_divs)}
    by_div = defaultdict(lambda: {'total': 0, 'at_main': 0})
    for r in recs:
        if not r['div']:
            continue
        by_div[r['div']]['total'] += 1
        if r['court_idx'] is not None and r['court_idx'] in blanes:
            by_div[r['div']]['at_main'] += 1
    divisions = []
    for d, v in by_div.items():
        pct = round(v['at_main'] / v['total'] * 100) if v['total'] else 0
        divisions.append({
            'div': d, 'total': v['total'], 'at_main': v['at_main'],
            'pct': pct, 'priority_rank': rank_of.get(d, 0),
        })
    divisions.sort(key=lambda x: x['priority_rank'] or 999)
    total_all = sum(v['total'] for v in by_div.values())
    main_all = sum(v['at_main'] for v in by_div.values())
    overall_pct = round(main_all / total_all * 100) if total_all else 0
    return {
        'rule': 'Blanes saturation',
        'summary': '%d%% of all games at %s (%d / %d)'
                   % (overall_pct, ctx['main_venue'], main_all, total_all),
        'divisions': divisions,
    }


def _hr_blanes_priority(sat_metric):
    """Reads the saturation metric and reports whether the priority order is
    respected — higher-priority divisions should not have a lower Blanes %
    than lower-priority ones."""
    divs = sat_metric['divisions']  # already sorted by priority_rank ascending
    inversions = 0
    for i in range(len(divs) - 1):
        if divs[i]['pct'] < divs[i + 1]['pct']:
            inversions += 1
    if inversions == 0:
        summary = 'Priority order respected — older / boys divisions correctly favoured'
    else:
        summary = ('%d priority inversion%s — a lower-priority division got more Blanes time'
                   % (inversions, '' if inversions == 1 else 's'))
    return {
        'rule': 'Blanes priority by age/gender',
        'summary': summary,
        'divisions': divs,
    }


def _hr_rest_fairness(rr_recs):
    target = _REST_TARGET_GAP
    # (div, team_lower, day) -> {slots, name, div, day}
    by = defaultdict(lambda: {'slots': [], 'name': None, 'div': None, 'day': None})
    for r in rr_recs:
        if r['min'] is None:
            continue
        for tm in (r['t1'], r['t2']):
            if not tm:
                continue
            key = (r['div'], tm.lower(), r['day'])
            entry = by[key]
            entry['slots'].append(r['min'])
            if entry['name'] is None:
                entry['name'] = tm
                entry['div'] = r['div']
                entry['day'] = r['day']
    for k in by:
        by[k]['slots'].sort()
    div_agg = defaultdict(lambda: {'tight_pairs': 0, 'worst_gap': None})
    teams = []
    total_tight = 0
    overall_worst = None
    for entry in by.values():
        slots = entry['slots']
        for i in range(len(slots)):
            for j in range(i + 1, len(slots)):
                gap = slots[j] - slots[i]
                if overall_worst is None or gap < overall_worst:
                    overall_worst = gap
                d_agg = div_agg[entry['div']]
                if d_agg['worst_gap'] is None or gap < d_agg['worst_gap']:
                    d_agg['worst_gap'] = gap
                if gap < target:
                    total_tight += 1
                    d_agg['tight_pairs'] += 1
                    teams.append({
                        'div': entry['div'], 'team': entry['name'],
                        'day': entry['day'] + 1, 'gap_min': gap,
                        'between': '%s -> %s' % (_min_to_time(slots[i]), _min_to_time(slots[j])),
                    })
    divisions = [
        {'div': d, 'tight_pairs': v['tight_pairs'], 'worst_gap': v['worst_gap']}
        for d, v in div_agg.items()
    ]
    divisions.sort(key=lambda x: (-x['tight_pairs'], x['div']))
    teams.sort(key=lambda t: t['gap_min'])
    if total_tight:
        summary = ('%d tight gap%s tournament-wide; tightest %d min'
                   % (total_tight, '' if total_tight == 1 else 's', overall_worst))
    else:
        summary = 'No tight gaps — every team has comfortable rest (target %d min)' % target
    return {
        'rule': 'Rest fairness',
        'summary': summary,
        'divisions': divisions,
        'teams': teams,
    }


def _hr_early_slot(ctx, rr_recs):
    by_div = defaultdict(lambda: {'sum': 0, 'count': 0, 'max': 0})
    day_tail = {}  # day -> max slot idx used
    for r in rr_recs:
        si = _slot_index(ctx, r['day'], r['min'])
        if si is None:
            continue
        agg = by_div[r['div']]
        agg['sum'] += si
        agg['count'] += 1
        if si > agg['max']:
            agg['max'] = si
        if si > day_tail.get(r['day'], -1):
            day_tail[r['day']] = si
    divisions = []
    for d, v in by_div.items():
        avg = round(v['sum'] / v['count'], 1) if v['count'] else 0
        divisions.append({'div': d, 'avg_slot_idx': avg, 'latest_slot_used': v['max']})
    divisions.sort(key=lambda x: -x['avg_slot_idx'])
    parts = []
    for d in sorted(day_tail.keys()):
        idx = day_tail[d]
        slots = ctx['day_slots'][d] if d < len(ctx['day_slots']) else []
        slot_min = slots[idx] if 0 <= idx < len(slots) else None
        parts.append('Day %d -> %s' % (d + 1, _min_to_time(slot_min) if slot_min is not None else '?'))
    summary = 'Latest slot used: ' + ', '.join(parts) if parts else 'No round-robin games found'
    return {
        'rule': 'Early-slot preference',
        'summary': summary,
        'divisions': divisions,
    }


def _hr_back_to_back(ctx, rr_recs):
    # (court_idx, day) -> {slot_min: division}
    by_cd = defaultdict(dict)
    for r in rr_recs:
        if r['div'] in _OLDER_DIVS and r['court_idx'] is not None and r['min'] is not None:
            by_cd[(r['court_idx'], r['day'])][r['min']] = r['div']
    pairs = []
    for (ci, day), slots_map in by_cd.items():
        if not isinstance(day, int) or day < 0 or day >= len(ctx['day_slots']):
            continue
        day_slots = ctx['day_slots'][day]
        for s in range(len(day_slots) - 1):
            if day_slots[s + 1] - day_slots[s] > 90:
                continue   # lunch-separated — solver doesn't penalise
            a, b = day_slots[s], day_slots[s + 1]
            if a in slots_map and b in slots_map:
                pairs.append({
                    'court': ctx['courts'][ci]['name'] if ci < len(ctx['courts']) else '?',
                    'day': day + 1,
                    'slot1': _min_to_time(a), 'slot1_div': slots_map[a],
                    'slot2': _min_to_time(b), 'slot2_div': slots_map[b],
                })
    pairs.sort(key=lambda p: (p['day'], p['court'], p['slot1']))
    summary = ('%d back-to-back same-court pair%s among U16 / U18 games'
               % (len(pairs), '' if len(pairs) == 1 else 's')) if pairs \
              else 'No back-to-back U16 / U18 same-court pairs'
    return {
        'rule': 'Back-to-back older divisions',
        'summary': summary,
        'pairs': pairs,
    }


def _hr_finals_atmosphere(ctx, recs):
    target_final = ctx['final_target']
    target_third = ctx['third_target']
    target_final_s = _min_to_time(target_final)
    target_third_s = _min_to_time(target_third)
    by_div = {}
    for r in recs:
        if r['min'] is None:
            continue
        lbl = r['lbl'].upper()
        if lbl == 'FINAL':
            by_div.setdefault(r['div'], {})['final_min'] = r['min']
            by_div[r['div']]['final_time'] = _min_to_time(r['min'])
        elif lbl.startswith('3RD') or '3RD PLACE' in lbl:
            by_div.setdefault(r['div'], {})['third_min'] = r['min']
            by_div[r['div']]['third_time'] = _min_to_time(r['min'])
    divisions = []
    on_target_count = finals_count = 0
    for d, v in by_div.items():
        if 'final_min' in v:
            finals_count += 1
            on_t = v['final_min'] == target_final
            if on_t:
                on_target_count += 1
        else:
            on_t = False
        divisions.append({
            'div': d,
            'final_time': v.get('final_time', '-'),
            'target': target_final_s,
            'third_time': v.get('third_time', '-'),
            'third_target': target_third_s,
            'on_target': on_t,
        })
    divisions.sort(key=lambda x: x['div'])
    if finals_count:
        summary = ('%d of %d finals at the target slot (%s)'
                   % (on_target_count, finals_count, target_final_s))
    else:
        summary = 'No final games found'
    return {
        'rule': 'Finals-day atmosphere',
        'summary': summary,
        'divisions': divisions,
    }


def report_health(config, games):
    """Per-division soft-rule breakdown for a finished schedule. Absolute view
    (no original-vs-edited comparison) — powers the Health section in the
    Audit Schedule modal."""
    ctx = _build_context(config)
    recs = _build_recs(games, ctx)
    rr = [r for r in recs if r['is_rr']]
    sat = _hr_blanes_saturation(ctx, recs)
    return {
        'metrics': [
            sat,
            _hr_blanes_priority(sat),
            _hr_rest_fairness(rr),
            _hr_early_slot(ctx, rr),
            _hr_back_to_back(ctx, rr),
            _hr_finals_atmosphere(ctx, recs),
        ]
    }
