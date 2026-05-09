"""
EYBC Tournament Scheduler — CP-SAT Constraint Solver

Iterative two-phase model using Google OR-Tools CP-SAT:
  Phase 1: RR placement — assign every RR game to (day, slot, court).
  Phase 2: PO placement — assign playoff games with RR frozen.
           Partial placement allowed; blocked games reported.

If Phase 2 can't place all PO games, the iterative wrapper extracts a
conflict certificate (which RR-occupied cells block which PO games),
feeds it back as forbidden cells for Phase 1, and re-solves. Up to 4
iterations before bailing out with whatever partial schedule we have.

Usage:
  from scheduler import solve_schedule
  result = solve_schedule(config)  # config dict from the frontend
"""

from ortools.sat.python import cp_model
from itertools import combinations
from collections import defaultdict
import json
import os
import time
import re


MAX_ITERATIONS = 4
UNPLACED_WEIGHT = 1_000_000  # dominates any soft-preference penalty


# ── Live progress state (read by /api/progress for UI banner) ────────────────
_progress_state = {
    'active': False,
    'iteration': 0,
    'max_iterations': MAX_ITERATIONS,
    'phase': '',
    'last_result': '',
    'started_at': None,
    'elapsed': 0,
}


def _progress(phase, **fields):
    """Update _progress_state and mirror the message to stdout."""
    _progress_state['phase'] = phase
    _progress_state.update(fields)
    if _progress_state.get('started_at'):
        _progress_state['elapsed'] = int(time.time() - _progress_state['started_at'])
    print(f"[Scheduler] {phase}")


def _progress_reset():
    _progress_state.update({
        'active': True,
        'iteration': 0,
        'max_iterations': MAX_ITERATIONS,
        'phase': 'Initializing...',
        'last_result': '',
        'started_at': time.time(),
        'elapsed': 0,
    })


def _progress_done(phase='Done'):
    _progress_state['active'] = False
    _progress_state['phase'] = phase


def _detect_blank_days(assembled, divisions, n_days):
    """Walk an assembled schedule and return blank (team, day) pairs.

    A team T in group X with positions 1..k is non-blank on day d iff:
      (a) Concrete: a game on d names T as t1 or t2.
      (b) Position-covered: ALL k positions of group X are covered by
          placeholder games on day d (regardless of which position T
          ends up at, T plays).
      (c) Downstream TBD: a game on d in T's division has empty t1/t2
          (Final / 3rd Place / medal final waiting on SF results).

    Mirrors measure_blank_days.measure() exactly so verification scripts
    and live solver runs agree on what constitutes a blank.
    """
    import re
    placeholder_re = re.compile(
        r'^(?:1st|2nd|3rd|4th|5th|6th|7th|8th)\s+Group\s+([A-Z])$'
    )

    sched = assembled.get('sched', {})
    team_to_group = {}
    group_to_size = {}
    all_teams = []
    for div in divisions:
        div_name = div.get('name', '')
        mg = div.get('manualGroups') or []
        for gi, grp in enumerate(mg):
            letter = chr(65 + gi)
            group_to_size[(div_name, letter)] = len(grp)
            for t in grp:
                team_to_group[(div_name, t)] = letter
                all_teams.append((div_name, t))

    concrete = {}
    position_coverage = {}
    div_tbd = {}

    for d_blk in sched.get('gameDays', []):
        d_idx = d_blk.get('dayIndex')
        if d_idx is None or d_idx >= n_days:
            continue
        for divb in d_blk.get('divs', []):
            dn = divb.get('name', '')
            buckets = []
            if divb.get('groups'):
                for gv in divb['groups'].values():
                    buckets.append(gv.get('games', []))
            elif divb.get('games'):
                buckets.append(divb['games'])
            for games in buckets:
                for g in games:
                    t1 = g.get('t1', '')
                    t2 = g.get('t2', '')
                    if not t1 and not t2:
                        div_tbd.setdefault(dn, [0] * n_days)[d_idx] += 1
                        continue
                    for raw in (t1, t2):
                        if not raw:
                            continue
                        m = placeholder_re.match(raw)
                        if m:
                            position = raw.split(' Group ', 1)[0].strip()
                            position_coverage.setdefault(
                                (dn, m.group(1), d_idx), set()
                            ).add(position)
                        else:
                            if (dn, raw) in team_to_group:
                                concrete.setdefault(
                                    (dn, raw), [0] * n_days
                                )[d_idx] += 1

    POSITION_LABELS = ['1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th']
    blank_pairs = []
    for div_name, team in all_teams:
        letter = team_to_group.get((div_name, team), '')
        group_size = group_to_size.get((div_name, letter), 1)
        required = set(POSITION_LABELS[:group_size])
        counts = concrete.get((div_name, team), [0] * n_days)
        tbd = div_tbd.get(div_name, [0] * n_days)
        for d_idx in range(n_days):
            if counts[d_idx] > 0 or tbd[d_idx] > 0:
                continue
            covered = position_coverage.get((div_name, letter, d_idx), set())
            if required.issubset(covered):
                continue
            blank_pairs.append({'divName': div_name, 'team': team, 'day': d_idx})
    return blank_pairs


def _apply_no_blank_day_check(assembled, ctx):
    """Run post-hoc no-blank-day detection on the assembled schedule.

    - Mandatory mode + any blanks: return a fail-loudly error response so
      the frontend can show the existing failure modal with the per-team
      diagnostic and the "Switch to High Priority and retry" button.
    - High Priority mode: attach blanks as `sched.noBlankDayWarnings` so
      audits / UI can surface them but generation still succeeds.
    - Rule disabled: pass-through.
    """
    if not ctx.get('rule_no_blank_day', False):
        return assembled
    blank_pairs = _detect_blank_days(assembled, ctx['divisions'], ctx['n_days'])
    if blank_pairs and ctx.get('nbd_mode', 'mandatory') == 'mandatory':
        _progress_done('Daily Game Distribution rule violated')
        return {
            'error': ('Daily Game Distribution rule (Mandatory) cannot be satisfied: '
                      f'{len(blank_pairs)} team-day pair(s) would have zero games.'),
            'reason': 'no_blank_day_mandatory_infeasible',
            'status': 'NBD_MANDATORY_VIOLATED',
            'blocked_team_days': blank_pairs,
        }
    # High Priority mode (or Mandatory with zero blanks): attach as warnings.
    assembled.setdefault('sched', {})['noBlankDayWarnings'] = blank_pairs
    return assembled


def solve_schedule(config, time_limit=120):
    """Iterative two-phase solver. Returns frontend-compatible schedule dict."""
    start_time = time.time()
    _progress_reset()
    ctx = _build_context(config)

    forbidden_cells = set()      # {(day, slot, court)} RR must avoid
    po_excluded_cells = set()    # {(p_idx, day, slot, court)} PO must avoid
    last_rr_result = None
    last_rr_occupied = None
    last_po_result = None
    last_blocked = None
    # Lever B: hints carried across iterations (None on iter 1).
    rr_hints = None  # {g: (d, s, c)} from previous iteration's RR placements
    po_hints = None  # {p: (d, s, c)} from previous iteration's PO placements
    # Lever C: detect plateau in blocked count → early bail.
    last_blocked_count = None

    for iteration in range(MAX_ITERATIONS):
        iter_start = time.time()
        _progress(
            f"=== Iteration {iteration+1}/{MAX_ITERATIONS} "
            f"(forbidden RR: {len(forbidden_cells)}, "
            f"PO exclusions: {len(po_excluded_cells)}) ===",
            iteration=iteration + 1,
        )

        rr_result, rr_occupied, p1_status, nbd_violations = _solve_phase1(
            ctx, forbidden_cells, time_limit, hints=rr_hints)

        if p1_status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            # Phase 1 infeasible — can't satisfy RR with these forbidden cells.
            # If this is the first iteration, it's a genuine infeasibility.
            # If later, we over-constrained Phase 1 via feedback — fall back.
            if iteration == 0:
                total_slots = sum(len(s) * ctx['num_courts'] for s in ctx['day_slots'])
                rr_slots = sum(len(ctx['rr_slot_mask'].get(d, set())) * ctx['num_courts']
                               for d in range(ctx['n_days']))
                _progress_done('Infeasible')
                return {'error': f"RR phase {ctx['status_names'].get(p1_status, 'INFEASIBLE')}. "
                                 f"{ctx['num_rr']} RR games need {rr_slots} court-slots "
                                 f"(total capacity: {total_slots}).",
                        'status': ctx['status_names'].get(p1_status, 'INFEASIBLE')}
            # Later iteration: use the last good result we have
            print(f"[Scheduler] Phase 1 became infeasible at iteration {iteration+1} — "
                  f"using last good result from iteration {iteration}")
            break

        # No-Blank-Day check now happens post-hoc (after Phase 2 places PO
        # games), in this function below the iterative loop.

        po_result, blocked, p2_status, po_placements = _solve_phase2(
            ctx, rr_result, rr_occupied, time_limit, po_excluded_cells,
            hints=po_hints)

        iter_summary = (f"Iteration {iteration+1} done in {time.time()-iter_start:.1f}s: "
                        f"{len(rr_result)} RR placed, {len(po_result)} PO placed, "
                        f"{len(blocked)} PO blocked")
        _progress(iter_summary,
                  last_result=f"{len(rr_result)} RR, {len(po_result)} PO, "
                              f"{len(blocked)} blocked")

        last_rr_result = rr_result
        last_rr_occupied = rr_occupied
        last_po_result = po_result
        last_blocked = blocked

        if not blocked:
            # All games placed.
            total_elapsed = time.time() - start_time
            _progress(f"Done: {len(rr_result)} RR + {len(po_result)} PO "
                      f"in {total_elapsed:.1f}s ({iteration+1} iteration(s))")
            _progress_done()
            assembled = _assemble_sched(rr_result, po_result, [], ctx, config)
            return _apply_no_blank_day_check(assembled, ctx)

        # Lever C: plateau detection — if blocked count didn't shrink, we're
        # not making progress. Bail before wasting another full iteration.
        if last_blocked_count is not None and len(blocked) >= last_blocked_count:
            _progress(f"No improvement vs previous iteration "
                      f"({last_blocked_count} → {len(blocked)} blocked) — bailing out")
            break
        last_blocked_count = len(blocked)

        # Derive feedback: RR cells to free up + PO games to evict from
        # cells that block lower/equal-priority PO games.
        new_forbidden, new_po_exclusions = _extract_conflict_cells(
            blocked, rr_occupied, po_placements, ctx)
        added_rr = new_forbidden - forbidden_cells
        added_po = new_po_exclusions - po_excluded_cells
        if not added_rr and not added_po:
            # No progress possible — bail with current partial schedule.
            _progress("No new feedback cells available — bailing out")
            break
        _progress(f"Adding {len(added_rr)} RR-forbid + "
                  f"{len(added_po)} PO-exclusion cell(s) for next iteration")
        forbidden_cells |= new_forbidden
        po_excluded_cells |= new_po_exclusions

        # Lever B: build hints for next iteration from this iter's placements.
        # The forbidden/exclusion sets above ensure the hinted cells will be
        # filtered by add_hint() calls in the next phases, so stale hints get
        # silently dropped.
        rr_hints = {g: (rg['day'], rg['slotIdx'], rg['courtIdx'])
                    for g, rg in enumerate(rr_result)}
        po_hints = dict(po_placements)  # already {p: (d, s, c)}

    # Exhausted iterations (or bailed) with blocked games — return best attempt.
    total_elapsed = time.time() - start_time
    n_rr = len(last_rr_result or [])
    n_po = len(last_po_result or [])
    n_blocked = len(last_blocked or [])
    _progress(f"Exhausted iterations: {n_rr} RR + {n_po} PO placed, "
              f"{n_blocked} PO blocked, total {total_elapsed:.1f}s")
    _progress_done()

    if last_rr_result is None:
        return {'error': 'Solver produced no schedule after iterative feedback.',
                'status': 'INFEASIBLE'}

    unscheduled = [_blocked_to_unsched(pg) for pg in (last_blocked or [])]
    assembled = _assemble_sched(last_rr_result, last_po_result or [], unscheduled, ctx, config)
    return _apply_no_blank_day_check(assembled, ctx)


# ══════════════════════════════════════════════════════════════════════════════
# CONTEXT BUILDER — shared setup used by both phases
# ══════════════════════════════════════════════════════════════════════════════

def _build_context(config):
    """Parse config once; return a context dict used by both phases."""
    divisions = config.get('divisions', [])
    sites_cfg = config.get('sites', [])
    sf = config.get('setupFields', {})
    venue_rules = config.get('venueRules', [])
    venue_blackouts = config.get('venueBlackouts', [])

    n_days = int(sf.get('nDays', 3))
    max_gpd = int(sf.get('maxGPD', 2))
    main_venue = sf.get('mainVenue', 'Blanes').strip()
    secondary_venue = (sf.get('secondaryVenue') or '').strip()
    rule_rest = sf.get('ruleRest', True)
    rule_venue_rest = sf.get('ruleVenueRest', True)
    # Daily Game Distribution (no blank days) — every team plays >=1 game per day.
    # Mandatory: hard constraint (slack pays a huge penalty so solver avoids it
    # unless absolutely impossible). After solving, any non-zero slack triggers
    # a generation failure with a per-(team, day) diagnostic.
    # High Priority: same shape, smaller penalty — solver minimizes blank days
    # but accepts unavoidable ones, surfaced as audit warnings.
    rule_no_blank_day = sf.get('ruleNoBlankDay', True)
    nbd_mode = sf.get('noBlankDayMode', 'mandatory')
    main_venue_final = sf.get('mainVenueFinal', True)
    main_venue_3rd = sf.get('mainVenue3rd', True)
    main_venue_sf = sf.get('mainVenueSF', False)
    # Per-category mode when toggle is ON: 'mandatory' (hard) or
    # 'high-priority' (soft, prefers main → secondary → anywhere).
    main_venue_final_mode = sf.get('mainVenueFinalMode', 'mandatory')
    main_venue_3rd_mode = sf.get('mainVenue3rdMode', 'mandatory')
    main_venue_sf_mode = sf.get('mainVenueSFMode', 'mandatory')
    lunch_start = _time_to_min(sf.get('lS', '13:30'))
    lunch_end = _time_to_min(sf.get('lE', '14:30'))

    # Finals target slot — drives FINAL_TARGET in _solve_phase2's soft objective.
    # The UI is now a single-slot dropdown but legacy snapshots may still send a
    # comma-separated list ("16:00, 14:30, 17:30"); pick the latest entry to best
    # match the "as late as possible" intent.
    final_times_str = (sf.get('finalTimes') or '17:30').strip()
    _ft_parts = [p.strip() for p in final_times_str.split(',') if p.strip()]
    try:
        final_target = max(_time_to_min(p) for p in _ft_parts) if _ft_parts else 1050
    except Exception:
        final_target = 1050
    third_target = max(0, final_target - 90)  # one 90-min slot earlier than the Final

    day_hours = sf.get('dayHours', [])
    if not day_hours:
        day_hours = [{'start': '09:00', 'end': '19:00' if i < n_days - 1 else '17:30'}
                     for i in range(n_days)]

    courts = []
    for site in sites_cfg:
        n = int(site.get('numCourts', 1))
        name = site.get('name', 'Unnamed')
        for i in range(n):
            court_name = f"{name} Court {i+1}" if n > 1 else name
            courts.append({'name': court_name, 'venue': name})
    num_courts = len(courts)
    main_venue_lower = main_venue.lower()
    blanes_courts = [i for i, c in enumerate(courts)
                     if c['venue'].lower().find(main_venue_lower) != -1]
    # Secondary venue courts (only used by the High-Priority tier).
    secondary_courts = []
    if secondary_venue and secondary_venue.lower() != main_venue_lower:
        sv_lower = secondary_venue.lower()
        secondary_courts = [i for i, c in enumerate(courts)
                            if c['venue'].lower() == sv_lower]

    day_slots = []
    for d in range(n_days):
        if d < len(day_hours):
            start_m = _time_to_min(day_hours[d].get('start', '09:00'))
            end_m = _time_to_min(day_hours[d].get('end', '19:00'))
        else:
            start_m, end_m = 540, 1140
        slots = []
        t = start_m
        while t <= end_m:
            if t < lunch_end and t + 90 > lunch_start:
                t = lunch_end
                continue
            slots.append(t)
            t += 90
        day_slots.append(slots)

    groups = {}
    team_div = {}
    all_teams = []
    for div in divisions:
        mg = div.get('manualGroups', [])
        if not mg:
            teams = div.get('teams', [])
            mg = [teams[i:i+4] for i in range(0, len(teams), 4)]
        for gi, grp in enumerate(mg):
            letter = chr(65 + gi)
            groups[(div['name'], letter)] = list(grp)
            for t in grp:
                team_div[(div['name'], t)] = letter
                all_teams.append((div['name'], t))

    rr_matchups = []
    for (div, grp), teams in groups.items():
        for a, b in combinations(teams, 2):
            rr_matchups.append((div, grp, a, b))
    num_rr = len(rr_matchups)

    # ── Division venue rules (Mandatory hard-gate + High Priority soft-pref) ──
    # Supports two shapes:
    #   NEW: {divName, venues: [...], mode: 'mandatory' | 'high-priority'}
    #   LEGACY: {divName, prio: 'blanes-only' | 'blanes-pref-1' | 'any' | 'V1,V2,...'}
    # Legacy rules are migrated transparently.
    mandatory_divs = set()
    div_allowed_courts = {}      # hard gate (Mandatory)
    div_preferred_courts = {}    # soft preference (High Priority)

    def _migrate_rule(vr):
        """Return a dict {divName, venues, mode} or None to drop the rule."""
        if 'venues' in vr and 'mode' in vr:
            return vr
        prio = vr.get('prio', 'any')
        dn = vr.get('divName', '')
        if not dn:
            return None
        if prio == 'blanes-only':
            return {'divName': dn, 'venues': [main_venue], 'mode': 'mandatory'}
        if prio == 'blanes-pref-1':
            return {'divName': dn, 'venues': [main_venue], 'mode': 'high-priority'}
        if isinstance(prio, str) and ',' in prio:
            return {'divName': dn,
                    'venues': [v.strip() for v in prio.split(',') if v.strip()],
                    'mode': 'mandatory'}
        return None  # 'any' or unknown → flexible (no rule)

    for raw in venue_rules:
        vr = _migrate_rule(raw)
        if vr is None:
            continue
        dn = vr['divName']
        venues_lower = {v.lower() for v in vr.get('venues', []) if v}
        if not venues_lower:
            continue
        court_set = {ci for ci, c in enumerate(courts)
                     if c['venue'].lower() in venues_lower}
        if not court_set:
            continue
        mode = vr.get('mode', 'high-priority')
        if mode == 'mandatory':
            div_allowed_courts[dn] = court_set
            mandatory_divs.add(dn)
        elif mode == 'high-priority':
            div_preferred_courts[dn] = court_set

    blackout_map = defaultdict(list)
    for bo in venue_blackouts:
        key = (bo['venue'], bo['day'])
        after = _time_to_min(bo['afterTime']) if bo.get('afterTime') else -1
        before = _time_to_min(bo['beforeTime']) if bo.get('beforeTime') else -1
        blackout_map[key].append((after, before))

    # Lever D helper: contiguous court-index groups for each multi-court venue.
    # Courts are appended sequentially per site in build order, so courts of
    # the same venue have consecutive indices. Within a venue all courts are
    # functionally interchangeable in the model (div_allowed_courts and
    # blackout_map both operate at venue granularity), so we can break the
    # search-tree symmetry safely. Single-court venues are skipped.
    venue_court_groups = []
    i = 0
    while i < num_courts:
        venue = courts[i]['venue']
        grp = [i]
        j = i + 1
        while j < num_courts and courts[j]['venue'] == venue:
            grp.append(j)
            j += 1
        if len(grp) >= 2:
            venue_court_groups.append(grp)
        i = j

    po_games = _build_po_structure(divisions, groups)

    finals_day = n_days - 1
    po_start_day = max(0, n_days - 2)

    total_rr_slots_needed = (num_rr + num_courts - 1) // num_courts
    day1_slots = len(day_slots[0]) if n_days > 0 else 0
    day2_slots = len(day_slots[1]) if n_days > 1 else 0

    if n_days >= 3:
        rr_capacity_with_2 = day1_slots * num_courts + (day2_slots - 2) * num_courts
        rr_capacity_with_1 = day1_slots * num_courts + (day2_slots - 1) * num_courts
        if num_rr <= rr_capacity_with_2:
            reserve = 2
        elif num_rr <= rr_capacity_with_1:
            reserve = 1
            print(f"[Scheduler] Reduced PO reservation to 1 slot")
        else:
            reserve = 0
            print(f"[Scheduler] WARNING: No PO reservation possible")
    else:
        reserve = min(2, day1_slots)

    rr_slot_mask = {}
    for d in range(n_days):
        if d < po_start_day:
            rr_slot_mask[d] = set(range(len(day_slots[d])))
        elif d == po_start_day and d < finals_day:
            n_slots = len(day_slots[d])
            rr_slot_mask[d] = set(range(n_slots - reserve))
        else:
            rr_slot_mask[d] = set()
    if n_days <= 2:
        rr_slot_mask[0] = set(range(len(day_slots[0]) - 2)) if len(day_slots[0]) > 2 else set()
        if n_days > 1:
            rr_slot_mask[1] = set()

    div_last_rr_day = {}
    for div in divisions:
        max_gs = max((len(g) for g in div.get('manualGroups', [[]])), default=1)
        div_last_rr_day[div['name']] = 0 if max_gs <= 1 else (((max_gs - 1) + max_gpd - 1) // max_gpd) - 1

    po_days_per_div = defaultdict(set)
    for div in divisions:
        div_name = div['name']
        last_rr = div_last_rr_day.get(div_name, 0)
        for d in range(n_days):
            if d >= po_start_day:
                if last_rr < d:
                    po_days_per_div[div_name].add(d)
                elif d == finals_day:
                    po_days_per_div[div_name].add(d)

    # Rank divisions (oldest → youngest, boys before girls on ties) and assign
    # exponentially-growing weights so the solver strongly favors Blanes for
    # top-priority divisions. Linear formula (age*2+boys) gave range 25..37,
    # which is too flat to overcome the search's other preferences.
    def _age_num(d):
        m = re.search(r'[Uu](\d+)', d['name'])
        return int(m.group(1)) if m else 0
    def _is_girls(d):
        n = d['name'].upper()
        return 'GIRL' in n or 'WOMEN' in n
    ranked = sorted(divisions, key=lambda d: (-_age_num(d), 1 if _is_girls(d) else 0))
    n_div = max(1, len(ranked))
    div_priority = {d['name']: 2 ** (n_div - 1 - idx) for idx, d in enumerate(ranked)}

    return {
        'config': config, 'divisions': divisions, 'sites_cfg': sites_cfg,
        'courts': courts, 'num_courts': num_courts,
        'blanes_courts': blanes_courts, 'main_venue': main_venue,
        'day_slots': day_slots, 'n_days': n_days,
        'max_gpd': max_gpd,
        'rule_rest': rule_rest, 'rule_venue_rest': rule_venue_rest,
        'rule_no_blank_day': rule_no_blank_day, 'nbd_mode': nbd_mode,
        'main_venue_final': main_venue_final, 'main_venue_3rd': main_venue_3rd,
        'main_venue_sf': main_venue_sf,
        'main_venue_final_mode': main_venue_final_mode,
        'main_venue_3rd_mode': main_venue_3rd_mode,
        'main_venue_sf_mode': main_venue_sf_mode,
        'secondary_venue': secondary_venue,
        'secondary_courts': secondary_courts,
        'lunch_start': lunch_start, 'lunch_end': lunch_end,
        'groups': groups, 'team_div': team_div, 'all_teams': all_teams,
        'rr_matchups': rr_matchups, 'num_rr': num_rr,
        'mandatory_divs': mandatory_divs, 'div_allowed_courts': div_allowed_courts,
        'div_preferred_courts': div_preferred_courts,
        'blackout_map': blackout_map,
        'po_games': po_games,
        'div_last_rr_day': div_last_rr_day, 'po_days_per_div': po_days_per_div,
        'rr_slot_mask': rr_slot_mask, 'finals_day': finals_day,
        'po_start_day': po_start_day, 'reserve': reserve,
        'div_priority': div_priority,
        'final_target': final_target, 'third_target': third_target,
        'venue_court_groups': venue_court_groups,
        'status_names': {cp_model.OPTIMAL: 'OPTIMAL', cp_model.FEASIBLE: 'FEASIBLE',
                         cp_model.INFEASIBLE: 'INFEASIBLE', cp_model.UNKNOWN: 'UNKNOWN',
                         cp_model.MODEL_INVALID: 'MODEL_INVALID'},
    }


def _is_blacked_out(ctx, court_idx, day_idx, slot_min):
    venue = ctx['courts'][court_idx]['venue']
    for after, before in ctx['blackout_map'].get((venue, day_idx), []):
        if after >= 0 and slot_min >= after:
            return True
        if before >= 0 and slot_min < before:
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — RR PLACEMENT
# ══════════════════════════════════════════════════════════════════════════════

def _solve_phase1(ctx, forbidden_cells, time_limit, hints=None):
    """
    Place RR games. forbidden_cells is a set of (day, slot, court) triples
    RR must avoid (passed from the iterative feedback loop).
    Returns (rr_result, rr_occupied, status).
    """
    divisions = ctx['divisions']
    courts = ctx['courts']
    num_courts = ctx['num_courts']
    blanes_courts = ctx['blanes_courts']
    day_slots = ctx['day_slots']
    n_days = ctx['n_days']
    max_gpd = ctx['max_gpd']
    rule_rest = ctx['rule_rest']
    rule_venue_rest = ctx['rule_venue_rest']
    rr_matchups = ctx['rr_matchups']
    num_rr = ctx['num_rr']
    div_allowed_courts = ctx['div_allowed_courts']
    rr_slot_mask = ctx['rr_slot_mask']
    po_days_per_div = ctx['po_days_per_div']
    div_priority = ctx['div_priority']

    _progress(f"Phase 1: placing {num_rr} RR games...")
    model = cp_model.CpModel()

    x = {}
    for g in range(num_rr):
        for d in range(n_days):
            for s in range(len(day_slots[d])):
                for c in range(num_courts):
                    x[g, d, s, c] = model.new_bool_var(f'x_{g}_{d}_{s}_{c}')

    # Lever B: warm-start with previous iteration's placements. Hints are
    # advisory — solver verifies them against constraints and ignores any
    # that don't satisfy. They speed convergence on iter 2+.
    if hints:
        for g, (d, s, c) in hints.items():
            key = (g, d, s, c)
            if key in x:
                model.add_hint(x[key], 1)

    for g in range(num_rr):
        model.add_exactly_one([
            x[g, d, s, c]
            for d in range(n_days) for s in range(len(day_slots[d])) for c in range(num_courts)
        ])

    # Ban RR from PO-reserved slots AND from forbidden cells (feedback loop).
    for g in range(num_rr):
        for d in range(n_days):
            allowed = rr_slot_mask.get(d, set())
            for s in range(len(day_slots[d])):
                if s not in allowed:
                    for c in range(num_courts):
                        model.add(x[g, d, s, c] == 0)
                else:
                    for c in range(num_courts):
                        if (d, s, c) in forbidden_cells:
                            model.add(x[g, d, s, c] == 0)

    # Court capacity.
    for d in range(n_days):
        for s in range(len(day_slots[d])):
            for c in range(num_courts):
                model.add_at_most_one([x[g, d, s, c] for g in range(num_rr)])

    # No team in two games at same (day, slot).
    team_games = defaultdict(list)
    for g, (div, grp, a, b) in enumerate(rr_matchups):
        team_games[(div, a)].append(g)
        team_games[(div, b)].append(g)

    for d in range(n_days):
        for s in range(len(day_slots[d])):
            for (div, team), g_list in team_games.items():
                if len(g_list) < 2:
                    continue
                terms = [x[g, d, s, c] for g in g_list for c in range(num_courts)]
                if terms:
                    model.add_at_most_one(terms)

    # Max games per team per day (Rule 10c on PO days).
    for d in range(n_days):
        for (div, team), g_list in team_games.items():
            terms = [x[g, d, s, c]
                     for g in g_list
                     for s in range(len(day_slots[d]))
                     for c in range(num_courts)]
            if not terms:
                continue
            effective_gpd = max_gpd
            if d in po_days_per_div.get(div, set()):
                effective_gpd = max(max_gpd - 1, 1)
            model.add(sum(terms) <= effective_gpd)

    # Daily Game Distribution (no blank days) — detection happens AFTER
    # both phases place games, in solve_schedule via _detect_blank_days().
    # The earlier Phase 1 slack-relaxed constraint here was incorrect (it
    # skipped days where the division had ANY PO game, on the wrong
    # assumption that PO covers every team in the division — but PO games
    # are group-specific). The post-hoc detection mirrors the helper logic
    # exactly: a team is non-blank on day d iff it has a concrete game on
    # day d, OR every position of its group has a placeholder PO game on
    # day d, OR there's a TBD downstream game (Final / 3rd Place / medal
    # final) in its division on day d.
    nbd_slacks = {}            # kept for return signature; always empty.
    nbd_penalty_terms = []     # kept for objective merge; always empty.

    # Rest between games (Rules 6 & 7).
    for (div, team), g_list in team_games.items():
        if len(g_list) < 2:
            continue
        for g1, g2 in combinations(g_list, 2):
            for d in range(n_days):
                for s1 in range(len(day_slots[d])):
                    m1 = day_slots[d][s1]
                    for s2 in range(len(day_slots[d])):
                        if s1 == s2:
                            continue
                        m2 = day_slots[d][s2]
                        gap = abs(m2 - m1)
                        for c1 in range(num_courts):
                            for c2 in range(num_courts):
                                same_v = courts[c1]['venue'] == courts[c2]['venue']
                                if rule_rest and same_v and gap < 180:
                                    model.add(x[g1, d, s1, c1] + x[g2, d, s2, c2] <= 1)
                                elif rule_venue_rest and not same_v and gap < 270:
                                    model.add(x[g1, d, s1, c1] + x[g2, d, s2, c2] <= 1)

    # Every team ≥ 1 RR at main venue.
    for (div, team), g_list in team_games.items():
        blanes_terms = [x[g, d, s, c]
                        for g in g_list
                        for d in range(n_days) for s in range(len(day_slots[d]))
                        for c in blanes_courts]
        if blanes_terms:
            model.add(sum(blanes_terms) >= 1)

    # Division venue restrictions.
    for g, (div, grp, a, b) in enumerate(rr_matchups):
        if div in div_allowed_courts:
            allowed = div_allowed_courts[div]
            for d in range(n_days):
                for s in range(len(day_slots[d])):
                    for c in range(num_courts):
                        if c not in allowed:
                            model.add(x[g, d, s, c] == 0)

    # Venue blackouts.
    for g in range(num_rr):
        for d in range(n_days):
            for s in range(len(day_slots[d])):
                for c in range(num_courts):
                    if _is_blacked_out(ctx, c, d, day_slots[d][s]):
                        model.add(x[g, d, s, c] == 0)

    # Lever D: symmetry breaking on interchangeable courts. Within each
    # multi-court venue, force courts to fill in canonical order at every
    # (day, slot): occupancy at court_k must be >= occupancy at court_{k+1}.
    # This collapses N! equivalent permutations of which game sits on which
    # court at the same venue, shrinking the search tree without excluding
    # any feasible schedule. We skip slots that are RR-forbidden (rr_slot_mask)
    # and pairs where forbidden_cells introduces asymmetry — applying the
    # constraint there would propagate forced zeros to free courts.
    for grp in ctx['venue_court_groups']:
        for d in range(n_days):
            allowed = rr_slot_mask.get(d, set())
            for s in range(len(day_slots[d])):
                if s not in allowed:
                    continue
                free = [c for c in grp if (d, s, c) not in forbidden_cells]
                if len(free) < 2:
                    continue
                for k in range(len(free) - 1):
                    c1, c2 = free[k], free[k + 1]
                    z1 = sum(x[g, d, s, c1] for g in range(num_rr))
                    z2 = sum(x[g, d, s, c2] for g in range(num_rr))
                    model.add(z1 >= z2)

    # Soft objective: prefer main venue for RR.
    outer_courts = [i for i in range(num_courts) if i not in blanes_courts]
    div_preferred_courts = ctx['div_preferred_courts']
    penalty_terms = []
    for g, (div, grp, a, b) in enumerate(rr_matchups):
        w = div_priority.get(div, 1)
        preferred = div_preferred_courts.get(div)
        if preferred is not None:
            # Division has an explicit High-Priority venue set — penalize
            # placements outside it.
            non_pref = [c for c in range(num_courts) if c not in preferred]
        else:
            # Default: penalize placements off the main venue.
            non_pref = outer_courts
        for d in range(n_days):
            for s in range(len(day_slots[d])):
                for c in non_pref:
                    penalty_terms.append(w * x[g, d, s, c])
    # Combine venue-preference penalties with No-Blank-Day slack penalties.
    all_penalty_terms = penalty_terms + nbd_penalty_terms
    if all_penalty_terms:
        model.minimize(sum(all_penalty_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    # Lever A: scale workers to available cores (CP-SAT scales near-linearly).
    solver.parameters.num_workers = min(16, os.cpu_count() or 8)
    status = solver.solve(model)
    _progress(f"Phase 1 status: {ctx['status_names'].get(status, status)}")

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return [], set(), status, []

    rr_result = []
    rr_occupied = set()
    for g, (div, grp, a, b) in enumerate(rr_matchups):
        for d in range(n_days):
            for s in range(len(day_slots[d])):
                for c in range(num_courts):
                    if solver.value(x[g, d, s, c]):
                        m = day_slots[d][s]
                        rr_result.append({
                            'day': d, 'slotIdx': s, 'courtIdx': c,
                            'time': _min_to_time(m), 'minutes': m,
                            'court': courts[c]['name'], 'loc': courts[c]['venue'],
                            'divName': div, 'group': f"{div} Group {grp}",
                            'color': _div_color(div, divisions),
                            't1': a, 't2': b,
                        })
                        rr_occupied.add((d, s, c))

    # Read No-Blank-Day slacks. Each non-zero slack means the rule could not
    # be satisfied for that (team, day) pair. The caller decides whether
    # this is a fatal error (Mandatory mode) or a soft warning (High Priority).
    nbd_violations = []
    for (div, team, d), slack in nbd_slacks.items():
        if solver.value(slack) > 0:
            nbd_violations.append({'divName': div, 'team': team, 'day': d})

    return rr_result, rr_occupied, status, nbd_violations


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — PO PLACEMENT (partial-placement allowed)
# ══════════════════════════════════════════════════════════════════════════════

def _solve_phase2(ctx, rr_result, rr_occupied, time_limit, po_excluded_cells=None, hints=None):
    """
    Place PO games given frozen RR. Uses at-most-one per PO (partial allowed)
    with a dominant placement-reward objective so the solver only skips a
    game when placement is strictly infeasible.

    po_excluded_cells: optional set of (p_idx, d, s, c) tuples — specific
    (PO game, cell) combinations that must NOT be used. Populated across
    iterations by the conflict-extraction feedback loop to force reshuffle
    when a blocked low-priority PO's legal cells are saturated by other PO
    games of same-or-lower priority.

    Returns (po_result, blocked, status, po_placements) where `blocked` is
    a list of po_games dicts that could not be placed, and `po_placements`
    is a dict mapping placed p_idx → (d, s, c).
    """
    divisions = ctx['divisions']
    courts = ctx['courts']
    num_courts = ctx['num_courts']
    blanes_courts = ctx['blanes_courts']
    day_slots = ctx['day_slots']
    n_days = ctx['n_days']
    finals_day = ctx['finals_day']
    div_allowed_courts = ctx['div_allowed_courts']
    rr_slot_mask = ctx['rr_slot_mask']
    po_days_per_div = ctx['po_days_per_div']
    div_priority = ctx['div_priority']
    groups = ctx['groups']
    po_games = ctx['po_games']
    num_po = len(po_games)

    _progress(f"Phase 2: placing {num_po} PO games...")
    po_model = cp_model.CpModel()

    y = {}
    for p in range(num_po):
        for d in range(n_days):
            for s in range(len(day_slots[d])):
                for c in range(num_courts):
                    y[p, d, s, c] = po_model.new_bool_var(f'po_{p}_{d}_{s}_{c}')

    # Lever B: warm-start with previous iteration's PO placements. Hints
    # are advisory — solver verifies and silently drops any that no longer
    # satisfy current constraints (e.g. cells now blocked by feedback).
    if hints:
        for p, (d, s, c) in hints.items():
            key = (p, d, s, c)
            if key in y:
                po_model.add_hint(y[key], 1)

    # Each PO placed at most once (partial placement allowed).
    for p in range(num_po):
        po_model.add_at_most_one([
            y[p, d, s, c]
            for d in range(n_days) for s in range(len(day_slots[d])) for c in range(num_courts)
        ])

    # PO-level cell exclusions from iterative feedback (PO-blocks-PO fix).
    if po_excluded_cells:
        for (p_idx, d, s, c) in po_excluded_cells:
            if 0 <= p_idx < num_po and 0 <= d < n_days \
                    and 0 <= s < len(day_slots[d]) and 0 <= c < num_courts:
                po_model.add(y[p_idx, d, s, c] == 0)

    # Court capacity: no conflict with RR or other PO.
    for d in range(n_days):
        for s in range(len(day_slots[d])):
            for c in range(num_courts):
                terms = [y[p, d, s, c] for p in range(num_po)]
                if (d, s, c) in rr_occupied:
                    po_model.add(sum(terms) == 0)
                else:
                    po_model.add_at_most_one(terms)

    # PO only in PO window (after RR boundary for each division).
    for p, pg in enumerate(po_games):
        div = pg['divName']
        for d in range(n_days):
            if d not in po_days_per_div.get(div, set()):
                for s in range(len(day_slots[d])):
                    for c in range(num_courts):
                        po_model.add(y[p, d, s, c] == 0)
            else:
                allowed_rr = rr_slot_mask.get(d, set())
                for s in range(len(day_slots[d])):
                    if s in allowed_rr:
                        for c in range(num_courts):
                            po_model.add(y[p, d, s, c] == 0)

    # Restricted divisions PO — only allowed courts.
    for p, pg in enumerate(po_games):
        if pg['divName'] in div_allowed_courts:
            allowed = div_allowed_courts[pg['divName']]
            for d in range(n_days):
                for s in range(len(day_slots[d])):
                    for c in range(num_courts):
                        if c not in allowed:
                            po_model.add(y[p, d, s, c] == 0)

    # Venue Exclusivity — driven by 3 toggles + per-toggle mode.
    # Toggle ON + mode='mandatory'  → hard constraint: must be at main venue.
    # Toggle ON + mode='high-priority' → soft tiers added below in the
    #   objective (main free, secondary medium penalty, elsewhere high).
    # Toggle OFF → no constraint, no penalty.
    venue_exclusivity_modes = {
        'Final': (ctx['main_venue_final'], ctx['main_venue_final_mode']),
        '3rd':   (ctx['main_venue_3rd'],   ctx['main_venue_3rd_mode']),
        'SF':    (ctx['main_venue_sf'],    ctx['main_venue_sf_mode']),
    }
    for p, pg in enumerate(po_games):
        t = pg.get('type')
        toggle_on, mode = venue_exclusivity_modes.get(t, (False, 'mandatory'))
        if toggle_on and mode == 'mandatory':
            # Hard: forbid all non-main-venue courts.
            for d in range(n_days):
                for s in range(len(day_slots[d])):
                    for c in range(num_courts):
                        if c not in blanes_courts:
                            po_model.add(y[p, d, s, c] == 0)

    # SF before Final/3rd with venue-aware gap (Rule 4 + 7).
    # Only enforced WHEN BOTH ARE PLACED: if SF is placed late, only Final cells
    # that are valid after it can be placed. If SF is unplaced, the constraint
    # collapses (both terms 0, sum<=1 trivially holds).
    for p1, pg1 in enumerate(po_games):
        if pg1.get('type') != 'SF':
            continue
        for p2, pg2 in enumerate(po_games):
            if pg2.get('type') == 'SF':
                continue
            if pg2['divName'] != pg1['divName']:
                continue
            if pg2.get('bracket') != pg1.get('bracket'):
                continue
            for d1 in range(n_days):
                for s1 in range(len(day_slots[d1])):
                    m1 = day_slots[d1][s1]
                    for d2 in range(n_days):
                        for s2 in range(len(day_slots[d2])):
                            m2 = day_slots[d2][s2]
                            for c1 in range(num_courts):
                                for c2 in range(num_courts):
                                    v1 = courts[c1]['venue']
                                    v2 = courts[c2]['venue']
                                    required = 180 if v1 == v2 else 270
                                    ok = (d1 < d2) or (d1 == d2 and m2 - m1 >= required)
                                    if not ok:
                                        po_model.add(y[p1, d1, s1, c1] + y[p2, d2, s2, c2] <= 1)

    # Venue blackouts for PO.
    for p in range(num_po):
        for d in range(n_days):
            for s in range(len(day_slots[d])):
                for c in range(num_courts):
                    if _is_blacked_out(ctx, c, d, day_slots[d][s]):
                        po_model.add(y[p, d, s, c] == 0)

    # Lever D: symmetry breaking on interchangeable courts. Same idea as in
    # Phase 1, but here we must skip (d, s) pairs where ANY court in the
    # group is RR-occupied or has a per-cell PO exclusion — that asymmetry
    # would propagate forced zeros to free courts and exclude valid
    # placements. When all courts in the group are unrestricted at (d, s)
    # we add the lex-ordering constraint that collapses court permutations.
    _po_excluded_cell_set = set()
    if po_excluded_cells:
        for (_p, _d, _s, _c) in po_excluded_cells:
            _po_excluded_cell_set.add((_d, _s, _c))
    for grp in ctx['venue_court_groups']:
        for d in range(n_days):
            for s in range(len(day_slots[d])):
                symmetric = True
                for c in grp:
                    if (d, s, c) in rr_occupied or (d, s, c) in _po_excluded_cell_set:
                        symmetric = False
                        break
                if not symmetric:
                    continue
                for k in range(len(grp) - 1):
                    c1, c2 = grp[k], grp[k + 1]
                    z1 = sum(y[p, d, s, c1] for p in range(num_po))
                    z2 = sum(y[p, d, s, c2] for p in range(num_po))
                    po_model.add(z1 >= z2)

    # Rest between RR and PO for candidate teams.
    rr_by_team_day = defaultdict(list)
    for rg in rr_result:
        for t in (rg['t1'], rg['t2']):
            rr_by_team_day[(rg['divName'], t, rg['day'])].append(
                (rg['slotIdx'], rg['minutes'], rg['courtIdx'], rg['loc'])
            )
    for p, pg in enumerate(po_games):
        candidate_teams = []
        for grp_letter in pg.get('groups', []):
            candidate_teams.extend(groups.get((pg['divName'], grp_letter), []))
        for team in candidate_teams:
            for d in range(n_days):
                for rr_s, rr_m, rr_c, rr_v in rr_by_team_day.get((pg['divName'], team, d), []):
                    for po_s in range(len(day_slots[d])):
                        po_m = day_slots[d][po_s]
                        for po_c in range(num_courts):
                            po_v = courts[po_c]['venue']
                            gap = abs(po_m - rr_m)
                            if po_s == rr_s:
                                po_model.add(y[p, d, po_s, po_c] == 0)
                            else:
                                required = 180 if po_v == rr_v else 270
                                if gap < required:
                                    po_model.add(y[p, d, po_s, po_c] == 0)

    # Objective: one placement indicator per PO (dominant), plus soft prefs on
    # cell-level y vars. Per-game indicators keep the objective compact and
    # give the solver a simple "maximize placements" signal.
    # Soft-pref 3 "showcase slot": Finals → admin-configured target slot,
    # 3rd Place → one 90-min slot earlier. The target comes from the Setup tab's
    # "Finals Target Time Slot" dropdown (sent as setupFields.finalTimes) and
    # is parsed in _build_context().
    # Distance-from-target penalty (symmetric) ensures Finals fill the target
    # slot first and 3rd Place fills one slot earlier, with 3rd-before-Final
    # reinforced. Per-division age weighting: when overflow forces medal games
    # to drop a slot, the YOUNGEST division gets pushed first (U18 keeps the
    # target slot, U12 accepts overflow). Older brackets are the showcase, by
    # tournament tradition.
    FINAL_TARGET = ctx['final_target']
    THIRD_TARGET = ctx['third_target']

    def _age_weight(divname):
        """Older brackets get higher weight → larger penalty for being away
        from their target → solver protects them first when 17:30 is saturated."""
        m_age = re.search(r'U(\d{2})', divname or '')
        if not m_age:
            return 1
        return {18: 4, 16: 3, 14: 2, 12: 1}.get(int(m_age.group(1)), 1)

    placed_vars = []
    for p in range(num_po):
        cell_vars = [y[p, d, s, c]
                     for d in range(n_days) for s in range(len(day_slots[d]))
                     for c in range(num_courts)]
        placed_p = po_model.new_bool_var(f'placed_{p}')
        po_model.add(sum(cell_vars) == placed_p)
        placed_vars.append(placed_p)

    obj_terms = []
    for p, pg in enumerate(po_games):
        w = UNPLACED_WEIGHT
        t = pg.get('type')
        if t in ('Final', '3rd'):
            w *= 100
        elif t == 'SF':
            w *= 10
        # Minimize -w * placed_p  ≡  reward placement.
        obj_terms.append(-w * placed_vars[p])

    # Tie-breaker helpers — both are tiny offsets that only matter when other
    # costs in the objective are equal, resolving previously-arbitrary
    # solver picks toward the user's stated preferences.
    def _bracket_blanes_bonus(bracket):
        """Bracket-importance tie-breaker for venue choice. Champ pays MORE
        for non-main-venue → solver prefers Champ at Blanes when capacity
        forces a same-division split. Tiny — only resolves ties."""
        b = (bracket or '').lower()
        if 'champ' in b:
            return 3
        if 'silver' in b or '5th' in b or '7th' in b:
            return 2
        if 'bronze' in b or '9th' in b or '11th' in b:
            return 1
        return 0  # 13th-place / consolation: lowest priority for Blanes

    def _is_boys_division(divname):
        n = (divname or '').upper()
        if 'GIRL' in n or 'WOMEN' in n:
            return False
        if 'MIX' in n:
            return False
        return True

    div_preferred_courts = ctx['div_preferred_courts']
    for p, pg in enumerate(po_games):
        t = pg.get('type')
        dn = pg['divName']
        preferred = div_preferred_courts.get(dn)
        for d in range(n_days):
            for s in range(len(day_slots[d])):
                m = day_slots[d][s]
                for c in range(num_courts):
                    soft = 0
                    # Venue soft-pref: honor division's High-Priority venue set
                    # when present; otherwise penalize non-main-venue.
                    if preferred is not None:
                        if c not in preferred:
                            soft += div_priority.get(dn, 1)
                    elif c not in blanes_courts:
                        soft += div_priority.get(dn, 1)
                    # Tie-breaker 1 (bracket importance): Championship pays
                    # extra to be at non-Blanes, Bronze pays a little, etc.
                    # Resolves the case where two same-division SFs (one
                    # Champ + one Bronze) compete for Blanes courts and the
                    # solver was picking arbitrarily.
                    if c not in blanes_courts:
                        soft += _bracket_blanes_bonus(pg.get('bracket', ''))
                    if t == 'Final' and d == finals_day:
                        # Symmetric distance from 17:30, weighted by age priority.
                        # 17:30 → 0; 16:00 → ±18×age_w; later than 17:30 → also penalized.
                        soft += _age_weight(dn) * (abs(m - FINAL_TARGET) // 5)
                    elif t == '3rd' and d == finals_day:
                        # Symmetric distance from 16:00, weighted by age priority.
                        # 16:00 → 0; 17:30 → 18×age_w (frees 17:30 for Finals);
                        # 14:30 → 18×age_w. Solver picks the later side first
                        # because of the existing 3rd-before-Final preference.
                        soft += _age_weight(dn) * (abs(m - THIRD_TARGET) // 5)
                        # Tie-breaker 2 (Boys over Girls at equal age):
                        # When two same-age 3rd Place games compete for the
                        # 16:00 target, Boys pays a tiny extra to be away
                        # from target → solver keeps Boys at the target slot
                        # and Girls overflows earlier.
                        if _is_boys_division(dn) and m != THIRD_TARGET:
                            soft += 1
                    elif t == 'SF' and d == finals_day:
                        # SF-position bias on finals day: earlier slots are
                        # strongly preferred so SFs don't cascade-block their
                        # own 3rd Place / Final placements (Rule 4 forces
                        # 3rd ≥180min after SF — a late SF pins 3rd to 17:30,
                        # stealing a Final-target slot).
                        soft += _age_weight(dn) * max(0, m - 540) // 15
                    # Reserve the Final-target slot on finals_day for Finals
                    # only. Any non-Final game placed at FINAL_TARGET pays a
                    # large penalty, forcing the solver to find SF / 3rd-Place
                    # / consolation placements that leave 17:30 exclusively for
                    # the showcase. Calibrated well below UNPLACED_WEIGHT so
                    # placement is never sacrificed, but well above any single
                    # distance penalty so it decisively flips the trade-off
                    # where a 3rd Place would otherwise camp on 17:30 (forced
                    # there by an SF placed at 14:30).
                    if t != 'Final' and d == finals_day and m == FINAL_TARGET:
                        soft += 5000
                    if soft:
                        obj_terms.append(soft * y[p, d, s, c])

    # Venue Exclusivity — High Priority tier (soft).
    # For PO games whose toggle is ON with mode='high-priority':
    #   main venue cells: 0 cost (preferred)
    #   secondary venue cells: SECONDARY_PENALTY (acceptable)
    #   elsewhere: ELSEWHERE_PENALTY (last resort)
    SECONDARY_PENALTY = 100
    ELSEWHERE_PENALTY = 500
    secondary_courts_set = set(ctx['secondary_courts'])
    blanes_courts_set = set(blanes_courts)
    for p, pg in enumerate(po_games):
        t = pg.get('type')
        toggle_on, mode = venue_exclusivity_modes.get(t, (False, 'mandatory'))
        if not (toggle_on and mode == 'high-priority'):
            continue
        for d in range(n_days):
            for s in range(len(day_slots[d])):
                for c in range(num_courts):
                    if c in blanes_courts_set:
                        continue  # main venue → no penalty
                    pen = SECONDARY_PENALTY if c in secondary_courts_set else ELSEWHERE_PENALTY
                    obj_terms.append(pen * y[p, d, s, c])

    # Soft: 3rd Place before Final (convention — Final is the tournament
    # climax). Uses global-slot int vars channeled to y; penalty fires only
    # when both games end up placed AND third's slot >= final's slot.
    slot_global = {}
    _gi = 0
    for d in range(n_days):
        for s in range(len(day_slots[d])):
            slot_global[(d, s)] = _gi
            _gi += 1
    max_global = _gi
    div_bracket_ft = {}
    for p, pg in enumerate(po_games):
        t = pg.get('type')
        if t in ('Final', '3rd'):
            key = (pg['divName'], pg.get('bracket', ''))
            div_bracket_ft.setdefault(key, {})[t] = p
    PENALTY_3RD_AFTER_FINAL = 100
    for key, pair in div_bracket_ft.items():
        pf = pair.get('Final')
        pt = pair.get('3rd')
        if pf is None or pt is None:
            continue
        final_g = po_model.new_int_var(0, max_global - 1, f'final_g_{pf}')
        third_g = po_model.new_int_var(0, max_global - 1, f'third_g_{pt}')
        for d in range(n_days):
            for s in range(len(day_slots[d])):
                gi_val = slot_global[(d, s)]
                for c in range(num_courts):
                    po_model.add(final_g == gi_val).only_enforce_if(y[pf, d, s, c])
                    po_model.add(third_g == gi_val).only_enforce_if(y[pt, d, s, c])
        bad_order = po_model.new_bool_var(f'bad_order_{pf}_{pt}')
        po_model.add(third_g >= final_g).only_enforce_if(bad_order)
        po_model.add(third_g < final_g).only_enforce_if(bad_order.Not())
        obj_terms.append(PENALTY_3RD_AFTER_FINAL * bad_order)

    if obj_terms:
        po_model.minimize(sum(obj_terms))

    po_solver = cp_model.CpSolver()
    po_solver.parameters.max_time_in_seconds = time_limit
    # Lever A: scale workers to available cores (CP-SAT scales near-linearly).
    po_solver.parameters.num_workers = min(16, os.cpu_count() or 8)
    po_status = po_solver.solve(po_model)
    _progress(f"Phase 2 status: {ctx['status_names'].get(po_status, po_status)}")

    if po_status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # Model infeasible outright (should be rare with at_most_one).
        return [], list(po_games), po_status, {}

    po_result = []
    blocked = []
    po_placements = {}
    for p, pg in enumerate(po_games):
        placed = False
        for d in range(n_days):
            if placed:
                break
            for s in range(len(day_slots[d])):
                if placed:
                    break
                for c in range(num_courts):
                    if po_solver.value(y[p, d, s, c]):
                        m = day_slots[d][s]
                        po_result.append({
                            'day': d, 'slotIdx': s, 'courtIdx': c,
                            'time': _min_to_time(m), 'minutes': m,
                            'court': courts[c]['name'], 'loc': courts[c]['venue'],
                            'divName': pg['divName'],
                            'color': _div_color(pg['divName'], divisions),
                            'lbl': pg['lbl'], 'bracket': pg.get('bracket', ''),
                            't1': pg.get('t1', ''), 't2': pg.get('t2', ''),
                        })
                        po_placements[p] = (d, s, c)
                        placed = True
                        break
        if not placed:
            blocked.append(pg)
    return po_result, blocked, po_status, po_placements


# ══════════════════════════════════════════════════════════════════════════════
# CONFLICT EXTRACTION — derive RR cells to forbid from blocked PO games
# ══════════════════════════════════════════════════════════════════════════════

def _po_priority_rank(pg):
    """Higher number = higher priority. Final/3rd > SF > Medal."""
    t = pg.get('type')
    if t in ('Final', '3rd'):
        return 3
    if t == 'SF':
        return 2
    return 1  # Medal / consolation


def _extract_conflict_cells(blocked, rr_occupied, po_placements, ctx):
    """
    Derive two feedback sets from the blocked PO games:

    1. `new_forbidden` — (day, slot, court) cells Phase 1 should free up in
       the next iteration (RR currently sits there, blocking PO legal cells).

    2. `new_po_exclusions` — (p_idx, day, slot, court) tuples of OTHER PO
       games that are currently placed in cells a blocked PO would legally
       use. Only flagged when the occupying PO is same-or-lower priority
       than the blocked one, so we never displace a Final for a Medal.

    Priority ordering of the blocked list: Final / 3rd → SF → Medal so the
    highest-stakes games get their feedback applied first.
    """
    divisions = ctx['divisions']
    num_courts = ctx['num_courts']
    blanes_courts_set = set(ctx['blanes_courts'])
    day_slots = ctx['day_slots']
    n_days = ctx['n_days']
    div_allowed_courts = ctx['div_allowed_courts']
    po_days_per_div = ctx['po_days_per_div']
    rr_slot_mask = ctx['rr_slot_mask']
    po_games = ctx['po_games']

    # Reverse-index placements: cell → p_idx (so we can ask "who's in this cell?").
    cell_to_po = {cell: p_idx for p_idx, cell in po_placements.items()}

    # Sort blocked by priority DESC (highest first).
    blocked_sorted = sorted(blocked, key=lambda g: -_po_priority_rank(g))

    new_forbidden = set()
    new_po_exclusions = set()

    for pg in blocked_sorted:
        div = pg['divName']
        allowed_courts = div_allowed_courts.get(div, set(range(num_courts)))
        allowed_days = po_days_per_div.get(div, set())
        is_final_or_3rd = pg.get('type') in ('Final', '3rd')
        blocked_prio = _po_priority_rank(pg)

        for d in allowed_days:
            rr_only = rr_slot_mask.get(d, set())
            for s in range(len(day_slots[d])):
                if s in rr_only:
                    continue  # PO can't use RR-masked slots anyway
                for c in range(num_courts):
                    if c not in allowed_courts:
                        continue
                    if is_final_or_3rd and c not in blanes_courts_set:
                        continue
                    if _is_blacked_out(ctx, c, d, day_slots[d][s]):
                        continue
                    cell = (d, s, c)
                    if cell in rr_occupied:
                        new_forbidden.add(cell)
                    elif cell in cell_to_po:
                        occupying_p = cell_to_po[cell]
                        occupying_pg = po_games[occupying_p]
                        # Only evict same-or-lower priority. Never displace a
                        # Final to accommodate a Medal — that risks losing the
                        # higher-value game if it has no other legal cell.
                        if _po_priority_rank(occupying_pg) <= blocked_prio:
                            new_po_exclusions.add((occupying_p, d, s, c))
    return new_forbidden, new_po_exclusions


def _blocked_to_unsched(pg):
    """Convert a blocked PO game dict to the frontend 'unscheduledGames' shape.

    Emits BOTH `divName` and `div` keys so that frontend renderers using
    either naming convention work without breakage. Falls back to the
    bracket label if divName is somehow empty (defensive — shouldn't
    normally happen but prevents the UI showing 'undefined').
    """
    div_name = pg.get('divName') or pg.get('bracket') or '(unknown division)'
    return {
        'divName': div_name,
        'div': div_name,           # alias for the JS-greedy convention
        'lbl': pg.get('lbl', ''),
        'bracket': pg.get('bracket', ''),
        't1': pg.get('t1', ''),
        't2': pg.get('t2', ''),
        'reason': 'No legal (day, slot, court) cell after iterative feedback',
    }


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _time_to_min(t):
    if not t:
        return 0
    parts = str(t).split(':')
    return int(parts[0]) * 60 + int(parts[1] if len(parts) > 1 else 0)


def _min_to_time(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def _div_color(div_name, divisions):
    for d in divisions:
        if d['name'] == div_name:
            return d.get('color', '#666')
    return '#666'


def _build_po_structure(divisions, groups):
    """Build PO game list from division structure."""
    po_games = []
    for div in divisions:
        div_name = div['name']
        mg = div.get('manualGroups', [])
        n_groups = len(mg)
        if n_groups == 0:
            continue
        max_grp_size = max(len(g) for g in mg) if mg else 0
        group_letters = [chr(65 + i) for i in range(n_groups)]

        if n_groups <= 1:
            po_games.append({'divName': div_name, 'lbl': 'FINAL', 'type': 'Final',
                             'bracket': 'Championship', 'groups': group_letters,
                             't1': '1st Group A', 't2': '2nd Group A'})
        elif n_groups == 2 and max_grp_size == 5:
            po_games.append({'divName': div_name, 'lbl': 'FINAL', 'type': 'Final',
                             'bracket': 'Championship', 'groups': group_letters,
                             't1': '1st Group A', 't2': '1st Group B'})
            po_games.append({'divName': div_name, 'lbl': 'Semi Final', 'type': 'Final',
                             'bracket': 'Championship', 'groups': group_letters,
                             't1': '2nd Group A', 't2': '2nd Group B'})
        elif n_groups == 2 and max_grp_size <= 4:
            is_u18_girls = 'GIRL' in div_name.upper() and '18' in div_name
            if is_u18_girls:
                po_games.append({'divName': div_name, 'lbl': 'SF 1', 'type': 'SF',
                                 'bracket': 'Championship', 'groups': ['A', 'B'],
                                 't1': '1st Group A', 't2': '2nd Group B'})
                po_games.append({'divName': div_name, 'lbl': 'SF 2', 'type': 'SF',
                                 'bracket': 'Championship', 'groups': ['A', 'B'],
                                 't1': '1st Group B', 't2': '2nd Group A'})
            else:
                po_games.append({'divName': div_name, 'lbl': 'SF 1', 'type': 'SF',
                                 'bracket': 'Championship', 'groups': ['A', 'B'],
                                 't1': '1st Group A', 't2': '1st Group B'})
                po_games.append({'divName': div_name, 'lbl': 'SF 2', 'type': 'SF',
                                 'bracket': 'Championship', 'groups': ['A', 'B'],
                                 't1': '1st Group B', 't2': '1st Group A'})
            po_games.append({'divName': div_name, 'lbl': '3rd Place', 'type': '3rd',
                             'bracket': 'Championship', 'groups': group_letters,
                             't1': '', 't2': ''})
            po_games.append({'divName': div_name, 'lbl': 'FINAL', 'type': 'Final',
                             'bracket': 'Championship', 'groups': group_letters,
                             't1': '', 't2': ''})
            if max_grp_size >= 3:
                po_games.append({'divName': div_name, 'lbl': '5th Place', 'type': 'Medal',
                                 'bracket': '5th Place', 'groups': group_letters,
                                 't1': '3rd Group A', 't2': '3rd Group B'})
            if max_grp_size >= 4:
                po_games.append({'divName': div_name, 'lbl': '7th Place', 'type': 'Medal',
                                 'bracket': '7th Place', 'groups': group_letters,
                                 't1': '4th Group A', 't2': '4th Group B'})
        elif n_groups == 4:
            # Championship — full 4-game bracket (always for 4-group divisions).
            po_games.append({'divName': div_name, 'lbl': 'SF 1', 'type': 'SF',
                             'bracket': 'Championship (1st–4th)', 'groups': ['A', 'B'],
                             't1': '1st Group A', 't2': '1st Group B'})
            po_games.append({'divName': div_name, 'lbl': 'SF 2', 'type': 'SF',
                             'bracket': 'Championship (1st–4th)', 'groups': ['C', 'D'],
                             't1': '1st Group C', 't2': '1st Group D'})
            po_games.append({'divName': div_name, 'lbl': '3rd Place', 'type': '3rd',
                             'bracket': 'Championship (1st–4th)', 'groups': group_letters,
                             't1': '', 't2': ''})
            po_games.append({'divName': div_name, 'lbl': 'FINAL', 'type': 'Final',
                             'bracket': 'Championship (1st–4th)', 'groups': group_letters,
                             't1': '', 't2': ''})
            if max_grp_size >= 3:
                if max_grp_size >= 4:
                    # 16-team divisions: Silver and Bronze are both 2 paired games.
                    # Position numbers are nominal labels (the four runners-up never
                    # all face each other) — see Scheduling Logic & Rules modal for
                    # the earned-vs-nominal explanation. Pairing is by group letter
                    # (AB / CD), matching the 13–16 placement convention below.
                    po_games.append({'divName': div_name, 'lbl': '5th/6th', 'type': 'Medal',
                                     'bracket': 'Silver (5th–8th)', 'groups': group_letters,
                                     't1': '2nd Group A', 't2': '2nd Group B'})
                    po_games.append({'divName': div_name, 'lbl': '7th/8th', 'type': 'Medal',
                                     'bracket': 'Silver (5th–8th)', 'groups': group_letters,
                                     't1': '2nd Group C', 't2': '2nd Group D'})
                    po_games.append({'divName': div_name, 'lbl': '9th/10th', 'type': 'Medal',
                                     'bracket': 'Bronze (9th–12th)', 'groups': group_letters,
                                     't1': '3rd Group A', 't2': '3rd Group B'})
                    po_games.append({'divName': div_name, 'lbl': '11th/12th', 'type': 'Medal',
                                     'bracket': 'Bronze (9th–12th)', 'groups': group_letters,
                                     't1': '3rd Group C', 't2': '3rd Group D'})
                else:
                    # 12-team divisions: Silver and Bronze are both full 4-game
                    # brackets (earned placements via cross-group SFs).
                    po_games.append({'divName': div_name, 'lbl': 'SF 1', 'type': 'SF',
                                     'bracket': 'Silver (5th–8th)', 'groups': ['A', 'B'],
                                     't1': '2nd Group A', 't2': '2nd Group B'})
                    po_games.append({'divName': div_name, 'lbl': 'SF 2', 'type': 'SF',
                                     'bracket': 'Silver (5th–8th)', 'groups': ['C', 'D'],
                                     't1': '2nd Group C', 't2': '2nd Group D'})
                    po_games.append({'divName': div_name, 'lbl': '5th/6th', 'type': 'Medal',
                                     'bracket': 'Silver (5th–8th)', 'groups': group_letters,
                                     't1': '', 't2': ''})
                    po_games.append({'divName': div_name, 'lbl': '7th/8th', 'type': 'Medal',
                                     'bracket': 'Silver (5th–8th)', 'groups': group_letters,
                                     't1': '', 't2': ''})
                    po_games.append({'divName': div_name, 'lbl': 'SF 1', 'type': 'SF',
                                     'bracket': 'Bronze (9th–12th)', 'groups': ['A', 'B'],
                                     't1': '3rd Group A', 't2': '3rd Group B'})
                    po_games.append({'divName': div_name, 'lbl': 'SF 2', 'type': 'SF',
                                     'bracket': 'Bronze (9th–12th)', 'groups': ['C', 'D'],
                                     't1': '3rd Group C', 't2': '3rd Group D'})
                    po_games.append({'divName': div_name, 'lbl': '9th/10th', 'type': 'Medal',
                                     'bracket': 'Bronze (9th–12th)', 'groups': group_letters,
                                     't1': '', 't2': ''})
                    po_games.append({'divName': div_name, 'lbl': '11th/12th', 'type': 'Medal',
                                     'bracket': 'Bronze (9th–12th)', 'groups': group_letters,
                                     't1': '', 't2': ''})
            if max_grp_size >= 4:
                # 13–16 placement (16-team only): 2 paired games, AB / CD pairing.
                po_games.append({'divName': div_name, 'lbl': '13th/14th', 'type': 'Medal',
                                 'bracket': '13th–16th Place', 'groups': group_letters,
                                 't1': '4th Group A', 't2': '4th Group B'})
                po_games.append({'divName': div_name, 'lbl': '15th/16th', 'type': 'Medal',
                                 'bracket': '13th–16th Place', 'groups': group_letters,
                                 't1': '4th Group C', 't2': '4th Group D'})
    return po_games


def _assemble_sched(rr_result, po_result, unscheduled, ctx, config):
    """Assemble the schedule in the format the frontend expects.

    No-Blank-Day warnings are added downstream by _apply_no_blank_day_check
    in solve_schedule (post-hoc detection), so this function intentionally
    leaves `sched.noBlankDayWarnings` unset.
    """
    divisions = ctx['divisions']
    courts = ctx['courts']
    day_slots = ctx['day_slots']
    n_days = ctx['n_days']

    game_days = []
    for d in range(n_days):
        is_last = d == n_days - 1
        rr_today = [g for g in rr_result if g['day'] == d]
        po_today = [g for g in po_result if g['day'] == d]

        if not rr_today and not po_today:
            if is_last:
                game_days.append({'label': f'Day {d+1} — Playoffs', 'divs': [], 'dayIndex': d})
            continue

        has_rr = len(rr_today) > 0
        has_po = len(po_today) > 0
        if is_last:
            label = f'Day {d+1} — {"Round Robin & " if has_rr else ""}Playoffs'
        else:
            label = f'Day {d+1} — Round Robin{"" if not has_po else " & Playoffs"}'

        by_div = {}
        for g in rr_today:
            dn = g['divName']
            if dn not in by_div:
                by_div[dn] = {'name': dn, 'color': g['color'], 'groups': {}}
            grp_key = g['group']
            if grp_key not in by_div[dn]['groups']:
                by_div[dn]['groups'][grp_key] = {'group': grp_key, 'games': []}
            by_div[dn]['groups'][grp_key]['games'].append({
                'time': g['time'], 't1': g['t1'], 't2': g['t2'],
                'court': g['court'], 'loc': g['loc'],
                'score1': None, 'score2': None, 'locked': False,
                'quotaViolation': False,
            })

        po_gk = lambda dn: f"{dn} — Playoffs"
        for g in po_today:
            dn = g['divName']
            if dn not in by_div:
                by_div[dn] = {'name': dn, 'color': g['color'], 'groups': {}}
            gk = po_gk(dn)
            if gk not in by_div[dn]['groups']:
                by_div[dn]['groups'][gk] = {'group': gk, 'games': []}
            by_div[dn]['groups'][gk]['games'].append({
                'time': g['time'], 't1': g['t1'], 't2': g['t2'],
                'lbl': g['lbl'], 'bracket': g.get('bracket', ''),
                'court': g['court'], 'loc': g['loc'],
                'score1': None, 'score2': None, 'locked': False,
                'dayLabel': f'Day {d+1}',
            })

        divs = list(by_div.values())
        game_days.append({'label': label, 'divs': divs, 'dayIndex': d})

    bracket_div_map = {}
    po_gk = lambda dn: f"{dn} — Playoffs"
    for de in game_days:
        for d in de['divs']:
            gk = po_gk(d['name'])
            if gk in d.get('groups', {}):
                po_game_refs = d['groups'][gk]['games']
                if d['name'] not in bracket_div_map:
                    div_def = next((dv for dv in divisions if dv['name'] == d['name']), None)
                    bracket_div_map[d['name']] = {
                        'name': d['name'], 'color': d['color'],
                        'teams': div_def.get('teams', []) if div_def else [],
                        'games': po_game_refs
                    }
                else:
                    bracket_div_map[d['name']]['games'] = bracket_div_map[d['name']]['games'] + po_game_refs

    bracket_days = []
    if bracket_div_map:
        last_label = game_days[-1]['label'] if game_days else 'Playoffs'
        bracket_days = [{'label': last_label, 'divs': list(bracket_div_map.values())}]

    courts_list = [{'court': c['name'], 'loc': c['venue']} for c in courts]
    day_slot_times = [[_min_to_time(m) for m in slots] for slots in day_slots]

    return {
        'sched': {
            'gameDays': game_days,
            'bracketDays': bracket_days,
            'totalCourts': len(courts),
            'courts': courts_list,
            'venueRestMandatory': config.get('setupFields', {}).get('venueRestMandatory', True),
            'daySlots': day_slot_times,
        },
        'divisions': divisions,
        'venueRules': config.get('venueRules', []),
        'venueBlackouts': config.get('venueBlackouts', []),
        'unscheduledGames': unscheduled,
        'sites': config.get('sites', []),
        'setupFields': config.get('setupFields', {}),
        'status': 'optimal' if not unscheduled else 'partial',
        'stats': {
            'rr_games': len(rr_result),
            'po_games': len(po_result),
            'total': len(rr_result) + len(po_result),
            'unscheduled': len(unscheduled),
        },
    }


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python scheduler.py config.json [out.json]")
        sys.exit(1)
    with open(sys.argv[1]) as f:
        config = json.load(f)
    result = solve_schedule(config)
    if 'error' in result:
        print(f"ERROR: {result['error']}")
        sys.exit(1)
    out_file = sys.argv[2] if len(sys.argv) > 2 else 'schedule_output.json'
    with open(out_file, 'w') as f:
        json.dump(result, f, indent=2)
    print(f"Schedule saved to {out_file}")
