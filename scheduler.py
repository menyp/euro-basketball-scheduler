"""
EYBC Tournament Scheduler — CP-SAT Constraint Solver

Two-phase constraint programming model using Google OR-Tools CP-SAT:
  Phase 1: RR placement — assign every RR game to (day, slot, court)
  Phase 2: PO placement — assign playoff games with RR frozen

All hard rules encoded as simultaneous constraints. Soft preferences
encoded as objective function terms to minimize.

Usage:
  from scheduler import solve_schedule
  result = solve_schedule(config)  # config dict from the frontend
"""

from ortools.sat.python import cp_model
from itertools import combinations
from collections import defaultdict
import json
import time


def solve_schedule(config, time_limit=120):
    """
    Main entry point. Takes a config dict and returns a schedule dict
    compatible with the frontend's importScheduleFromJSON format.

    config keys:
      divisions: [{name, color, teams, manualGroups}]
      sites: [{name, numCourts}]
      setupFields: {nDays, maxGPD, lS, lE, mainVenue, ruleRest, ruleVenueRest,
                     venueRestMandatory, mainVenueFinal, mainVenue3rd, mainVenueSF,
                     dayHours: [{start, end}], finalTimes}
      venueRules: [{divName, prio}]
      venueBlackouts: [{venue, day, afterTime, beforeTime}]
    """
    start_time = time.time()

    # ── Parse config ──────────────────────────────────────────────────────────
    divisions = config.get('divisions', [])
    sites_cfg = config.get('sites', [])
    sf = config.get('setupFields', {})
    venue_rules = config.get('venueRules', [])
    venue_blackouts = config.get('venueBlackouts', [])

    n_days = int(sf.get('nDays', 3))
    max_gpd = int(sf.get('maxGPD', 2))
    main_venue = sf.get('mainVenue', 'Blanes').strip()
    rule_rest = sf.get('ruleRest', True)
    rule_venue_rest = sf.get('ruleVenueRest', True)
    main_venue_final = sf.get('mainVenueFinal', True)
    main_venue_3rd = sf.get('mainVenue3rd', True)
    lunch_start = _time_to_min(sf.get('lS', '13:30'))
    lunch_end = _time_to_min(sf.get('lE', '14:30'))

    day_hours = sf.get('dayHours', [])
    if not day_hours:
        day_hours = [{'start': '09:00', 'end': '19:00' if i < n_days - 1 else '17:30'}
                     for i in range(n_days)]

    # ── Build courts ──────────────────────────────────────────────────────────
    courts = []  # [{name, venue}]
    for site in sites_cfg:
        n = int(site.get('numCourts', 1))
        name = site.get('name', 'Unnamed')
        for i in range(n):
            court_name = f"{name} Court {i+1}" if n > 1 else name
            courts.append({'name': court_name, 'venue': name})

    num_courts = len(courts)
    main_venue_lower = main_venue.lower()
    blanes_courts = [i for i, c in enumerate(courts) if c['venue'].lower().find(main_venue_lower) != -1]

    # ── Build time slots per day ──────────────────────────────────────────────
    day_slots = []  # day_slots[d] = [minutes, minutes, ...]
    for d in range(n_days):
        if d < len(day_hours):
            start_m = _time_to_min(day_hours[d].get('start', '09:00'))
            end_m = _time_to_min(day_hours[d].get('end', '19:00'))
        else:
            start_m, end_m = 540, 1140
        slots = []
        t = start_m
        while t <= end_m:
            # Skip lunch window
            if t < lunch_end and t + 90 > lunch_start:
                t = lunch_end
                continue
            slots.append(t)
            t += 90
        day_slots.append(slots)

    # ── Build groups and matchups ─────────────────────────────────────────────
    groups = {}       # (div_name, group_letter) -> [team_names]
    team_div = {}     # (div_name, team_name) -> group_letter
    all_teams = []    # [(div_name, team_name)]

    for div in divisions:
        mg = div.get('manualGroups', [])
        if not mg:
            # Fallback: chunk teams into groups of 4
            teams = div.get('teams', [])
            size = 4
            mg = [teams[i:i+size] for i in range(0, len(teams), size)]
        for gi, grp in enumerate(mg):
            letter = chr(65 + gi)
            groups[(div['name'], letter)] = list(grp)
            for t in grp:
                team_div[(div['name'], t)] = letter
                all_teams.append((div['name'], t))

    # RR matchups
    rr_matchups = []  # [(div, group_letter, team1, team2)]
    for (div, grp), teams in groups.items():
        for a, b in combinations(teams, 2):
            rr_matchups.append((div, grp, a, b))

    num_rr = len(rr_matchups)

    # ── Mandatory divisions ───────────────────────────────────────────────────
    mandatory_divs = set()
    for vr in venue_rules:
        if vr.get('prio') == 'blanes-only':
            mandatory_divs.add(vr['divName'])

    # ── Venue blackouts ───────────────────────────────────────────────────────
    blackout_map = defaultdict(list)  # (venue, day) -> [(after_min, before_min)]
    for bo in venue_blackouts:
        key = (bo['venue'], bo['day'])
        after = _time_to_min(bo['afterTime']) if bo.get('afterTime') else -1
        before = _time_to_min(bo['beforeTime']) if bo.get('beforeTime') else -1
        blackout_map[key].append((after, before))

    def is_blacked_out(court_idx, day_idx, slot_min):
        venue = courts[court_idx]['venue']
        for after, before in blackout_map.get((venue, day_idx), []):
            if after >= 0 and slot_min >= after:
                return True
            if before >= 0 and slot_min < before:
                return True
        return False

    # ── PO structure ──────────────────────────────────────────────────────────
    po_games = _build_po_structure(divisions, groups)

    # ── Group-aware PO day map for Rule 10c ───────────────────────────────────
    # Only cap teams at maxGPD-1 on days where their SPECIFIC GROUP has a PO game.
    # This gives the solver more freedom for groups that don't have PO on that day.
    po_days_per_group = {}  # (div, group_letter, day) → True

    # ── Determine PO days and RR slot mask ────────────────────────────────────
    # Reserve last 2 slots of Day N-2 and all of Day N-1 for PO
    # RR gets: Day 1 all slots + Day 2 slots 0..(len-3) for 3-day tournaments
    finals_day = n_days - 1
    po_start_day = max(0, n_days - 2)

    rr_slot_mask = {}
    for d in range(n_days):
        if d < po_start_day:
            rr_slot_mask[d] = set(range(len(day_slots[d])))
        elif d == po_start_day and d < finals_day:
            # Allow RR on most of this day, reserve tail for PO
            n_slots = len(day_slots[d])
            reserve = min(2, n_slots)  # reserve last 2 slots for PO
            rr_slot_mask[d] = set(range(n_slots - reserve))
        else:
            rr_slot_mask[d] = set()  # finals day: no RR

    # For 2-day tournaments, adjust
    if n_days <= 2:
        rr_slot_mask[0] = set(range(len(day_slots[0]) - 2)) if len(day_slots[0]) > 2 else set()
        if n_days > 1:
            rr_slot_mask[1] = set()

    # ── Determine divLastRRDay ───────────────────────────────────────────────
    div_last_rr_day = {}
    for div in divisions:
        max_gs = max((len(g) for g in div.get('manualGroups', [[]])), default=1)
        div_last_rr_day[div['name']] = 0 if max_gs <= 1 else (((max_gs - 1) + max_gpd - 1) // max_gpd) - 1

    # Divisions with PO on specific days — division-aware:
    # Divisions that finish RR before Day N-2 (3-team groups) can have PO on Day N-2.
    # Divisions that extend RR to Day N-2 (4-team groups) have PO on Day N-1 only.
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

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 1: RR PLACEMENT
    # ══════════════════════════════════════════════════════════════════════════
    print(f"[Scheduler] Phase 1: placing {num_rr} RR games...")

    model = cp_model.CpModel()

    # Decision variables: x[g, d, s, c] = 1 iff game g at day d, slot s, court c
    x = {}
    for g in range(num_rr):
        for d in range(n_days):
            for s in range(len(day_slots[d])):
                for c in range(num_courts):
                    x[g, d, s, c] = model.new_bool_var(f'x_{g}_{d}_{s}_{c}')

    # ── Rule 1: Each RR game placed exactly once ──────────────────────────────
    for g in range(num_rr):
        model.add_exactly_one([
            x[g, d, s, c]
            for d in range(n_days) for s in range(len(day_slots[d])) for c in range(num_courts)
        ])

    # ── RR slot mask: ban RR from PO-reserved slots ───────────────────────────
    for g in range(num_rr):
        for d in range(n_days):
            allowed = rr_slot_mask.get(d, set())
            for s in range(len(day_slots[d])):
                if s not in allowed:
                    for c in range(num_courts):
                        model.add(x[g, d, s, c] == 0)

    # ── Rule 10: Court capacity (at most 1 game per court per slot) ───────────
    for d in range(n_days):
        for s in range(len(day_slots[d])):
            for c in range(num_courts):
                model.add_at_most_one([x[g, d, s, c] for g in range(num_rr)])

    # ── Rule 2: No team in two games at same (day, slot) ──────────────────────
    team_games = defaultdict(list)  # (div, team) -> [game_indices]
    for g, (div, grp, a, b) in enumerate(rr_matchups):
        team_games[(div, a)].append(g)
        team_games[(div, b)].append(g)

    for d in range(n_days):
        for s in range(len(day_slots[d])):
            for (div, team), g_list in team_games.items():
                if len(g_list) < 2:
                    continue
                terms = []
                for g in g_list:
                    for c in range(num_courts):
                        terms.append(x[g, d, s, c])
                if terms:
                    model.add_at_most_one(terms)

    # ── Rule 5 + 10c: Max games per team per day ──────────────────────────────
    for d in range(n_days):
        for (div, team), g_list in team_games.items():
            terms = []
            for g in g_list:
                for s in range(len(day_slots[d])):
                    for c in range(num_courts):
                        terms.append(x[g, d, s, c])
            if terms:
                # Rule 10c: on ANY PO day for this division, limit RR to maxGPD-1
                # so advancing teams don't exceed maxGPD total (RR + SF).
                # Applied unconditionally — no divLastRRDay gate — to catch all
                # groups including 4-team groups whose SFs land on Day 2.
                effective_gpd = max_gpd
                if d in po_days_per_div.get(div, set()):
                    effective_gpd = max(max_gpd - 1, 1)
                model.add(sum(terms) <= effective_gpd)

    # ── Rules 6 & 7: Rest between consecutive games ──────────────────────────
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
                                v1 = courts[c1]['venue']
                                v2 = courts[c2]['venue']
                                same_venue = v1 == v2
                                if rule_rest and same_venue and gap < 180:
                                    model.add(x[g1, d, s1, c1] + x[g2, d, s2, c2] <= 1)
                                elif rule_venue_rest and not same_venue and gap < 270:
                                    model.add(x[g1, d, s1, c1] + x[g2, d, s2, c2] <= 1)

    # ── Rule 10b: Every team ≥ 1 RR at main venue ────────────────────────────
    for (div, team), g_list in team_games.items():
        blanes_terms = []
        for g in g_list:
            for d in range(n_days):
                for s in range(len(day_slots[d])):
                    for c in blanes_courts:
                        blanes_terms.append(x[g, d, s, c])
        if blanes_terms:
            model.add(sum(blanes_terms) >= 1)

    # ── Rule 13: Mandatory divisions at main venue only ───────────────────────
    for g, (div, grp, a, b) in enumerate(rr_matchups):
        if div in mandatory_divs:
            for d in range(n_days):
                for s in range(len(day_slots[d])):
                    for c in range(num_courts):
                        if c not in blanes_courts:
                            model.add(x[g, d, s, c] == 0)

    # ── Venue blackouts ───────────────────────────────────────────────────────
    for g in range(num_rr):
        for d in range(n_days):
            for s in range(len(day_slots[d])):
                for c in range(num_courts):
                    if is_blacked_out(c, d, day_slots[d][s]):
                        model.add(x[g, d, s, c] == 0)

    # ── Objective: soft preferences ───────────────────────────────────────────
    div_priority = {}
    for div in divisions:
        age = 0
        import re
        m = re.search(r'[Uu](\d+)', div['name'])
        if m:
            age = int(m.group(1))
        is_boys = 'GIRL' not in div['name'].upper() and 'WOMEN' not in div['name'].upper()
        div_priority[div['name']] = age * 2 + (1 if is_boys else 0)

    outer_courts = [i for i in range(num_courts) if i not in blanes_courts]
    penalty_terms = []
    for g, (div, grp, a, b) in enumerate(rr_matchups):
        w = div_priority.get(div, 1)
        for d in range(n_days):
            for s in range(len(day_slots[d])):
                for c in outer_courts:
                    penalty_terms.append(w * x[g, d, s, c])

    if penalty_terms:
        model.minimize(sum(penalty_terms))

    # ── Solve Phase 1 ─────────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = 8
    status = solver.solve(model)

    status_name = solver.status_name(status)
    print(f"[Scheduler] Phase 1 status: {status_name}")

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {'error': f'RR phase {status_name}. No feasible schedule found.',
                'status': status_name}

    # ── Extract RR assignments ────────────────────────────────────────────────
    rr_result = []
    rr_occupied = set()  # (d, s, c) occupied by RR
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

    rr_elapsed = time.time() - start_time
    print(f"[Scheduler] Phase 1 done: {len(rr_result)} RR games in {rr_elapsed:.1f}s")

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 2: PO PLACEMENT
    # ══════════════════════════════════════════════════════════════════════════
    print(f"[Scheduler] Phase 2: placing {len(po_games)} PO games...")

    po_model = cp_model.CpModel()
    num_po = len(po_games)

    # Decision variables
    y = {}
    for p in range(num_po):
        for d in range(n_days):
            for s in range(len(day_slots[d])):
                for c in range(num_courts):
                    y[p, d, s, c] = po_model.new_bool_var(f'po_{p}_{d}_{s}_{c}')

    # Each PO placed exactly once
    for p in range(num_po):
        po_model.add_exactly_one([
            y[p, d, s, c]
            for d in range(n_days) for s in range(len(day_slots[d])) for c in range(num_courts)
        ])

    # Court capacity: no conflict with RR or other PO
    for d in range(n_days):
        for s in range(len(day_slots[d])):
            for c in range(num_courts):
                terms = [y[p, d, s, c] for p in range(num_po)]
                if (d, s, c) in rr_occupied:
                    po_model.add(sum(terms) == 0)
                else:
                    po_model.add_at_most_one(terms)

    # PO only in PO window (after RR boundary for each division)
    for p, pg in enumerate(po_games):
        div = pg['divName']
        for d in range(n_days):
            if d not in po_days_per_div.get(div, set()):
                for s in range(len(day_slots[d])):
                    for c in range(num_courts):
                        po_model.add(y[p, d, s, c] == 0)
            else:
                # On PO days, only allow slots outside the RR mask
                allowed_rr = rr_slot_mask.get(d, set())
                for s in range(len(day_slots[d])):
                    if s in allowed_rr:
                        # This slot is for RR, ban PO (unless it's also in the PO window)
                        # Actually: if the slot is NOT in the RR mask, it's for PO
                        for c in range(num_courts):
                            po_model.add(y[p, d, s, c] == 0)

    # Rule 13: U18 BOYS PO at main venue
    for p, pg in enumerate(po_games):
        if pg['divName'] in mandatory_divs:
            for d in range(n_days):
                for s in range(len(day_slots[d])):
                    for c in range(num_courts):
                        if c not in blanes_courts:
                            po_model.add(y[p, d, s, c] == 0)

    # Rules 11, 12: Finals + 3rd Place at main venue
    for p, pg in enumerate(po_games):
        if pg.get('type') in ('Final', '3rd'):
            for d in range(n_days):
                for s in range(len(day_slots[d])):
                    for c in range(num_courts):
                        if c not in blanes_courts:
                            po_model.add(y[p, d, s, c] == 0)

    # Rule 4 + Rule 7 for PO: SF must start before later games in same bracket,
    # with venue-aware gap:
    #   Same venue: ≥ 180 min start-to-start (90 min actual rest)
    #   Different venue: ≥ 270 min start-to-start (180 min actual rest for travel)
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

    # Venue blackouts for PO
    for p in range(num_po):
        for d in range(n_days):
            for s in range(len(day_slots[d])):
                for c in range(num_courts):
                    if is_blacked_out(c, d, day_slots[d][s]):
                        po_model.add(y[p, d, s, c] == 0)

    # Rest between RR and PO (conservative: any team in PO groups)
    rr_by_team_day = defaultdict(list)  # (div, team, day) -> [(slot, minutes, court, venue)]
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

    # PO soft objective: Finals prime-time + Blanes priority
    po_penalty = []
    prime_slots = {870, 960, 1050}  # 14:30, 16:00, 17:30
    for p, pg in enumerate(po_games):
        for d in range(n_days):
            for s in range(len(day_slots[d])):
                m = day_slots[d][s]
                for c in range(num_courts):
                    w = 0
                    if c not in blanes_courts:
                        w += div_priority.get(pg['divName'], 1)
                    if pg.get('type') in ('Final', '3rd') and d == finals_day and m not in prime_slots:
                        w += 50
                    if w:
                        po_penalty.append(w * y[p, d, s, c])
    if po_penalty:
        po_model.minimize(sum(po_penalty))

    # ── Solve Phase 2 ─────────────────────────────────────────────────────────
    po_solver = cp_model.CpSolver()
    po_solver.parameters.max_time_in_seconds = time_limit
    po_solver.parameters.num_workers = 8
    po_status = po_solver.solve(po_model)

    po_status_name = po_solver.status_name(po_status)
    print(f"[Scheduler] Phase 2 status: {po_status_name}")

    if po_status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {'error': f'PO phase {po_status_name}. RR placed but PO infeasible.',
                'status': po_status_name,
                'rr_count': len(rr_result)}

    # ── Extract PO assignments ────────────────────────────────────────────────
    po_result = []
    for p, pg in enumerate(po_games):
        for d in range(n_days):
            for s in range(len(day_slots[d])):
                for c in range(num_courts):
                    if po_solver.value(y[p, d, s, c]):
                        m = day_slots[d][s]
                        po_result.append({
                            'day': d, 'slotIdx': s, 'courtIdx': c,
                            'time': _min_to_time(m), 'minutes': m,
                            'court': courts[c]['name'], 'loc': courts[c]['venue'],
                            'divName': pg['divName'], 'color': _div_color(pg['divName'], divisions),
                            'lbl': pg['lbl'], 'bracket': pg.get('bracket', ''),
                            't1': pg.get('t1', ''), 't2': pg.get('t2', ''),
                        })

    total_elapsed = time.time() - start_time
    print(f"[Scheduler] Done: {len(rr_result)} RR + {len(po_result)} PO in {total_elapsed:.1f}s")

    # ── Assemble output in frontend-compatible format ─────────────────────────
    return _assemble_sched(rr_result, po_result, divisions, courts, day_slots, n_days, config)


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
            # Single group: just a final
            po_games.append({'divName': div_name, 'lbl': 'FINAL', 'type': 'Final',
                             'bracket': 'Championship', 'groups': group_letters,
                             't1': '1st Group A', 't2': '2nd Group A'})
        elif n_groups == 2 and max_grp_size == 5:
            # 10-team: direct-seed
            po_games.append({'divName': div_name, 'lbl': 'FINAL', 'type': 'Final',
                             'bracket': 'Championship', 'groups': group_letters,
                             't1': '1st Group A', 't2': '1st Group B'})
            po_games.append({'divName': div_name, 'lbl': 'Semi Final', 'type': 'Final',
                             'bracket': 'Championship', 'groups': group_letters,
                             't1': '2nd Group A', 't2': '2nd Group B'})
        elif n_groups == 2 and max_grp_size <= 4:
            # 2 groups: cross-seeded SFs
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
            # Consolation
            if max_grp_size >= 3:
                po_games.append({'divName': div_name, 'lbl': '5th Place', 'type': 'Medal',
                                 'bracket': '5th Place', 'groups': group_letters,
                                 't1': '3rd Group A', 't2': '3rd Group B'})
            if max_grp_size >= 4:
                po_games.append({'divName': div_name, 'lbl': '7th Place', 'type': 'Medal',
                                 'bracket': '7th Place', 'groups': group_letters,
                                 't1': '4th Group A', 't2': '4th Group B'})
        elif n_groups == 4:
            # 4 groups: Championship + Silver + Bronze brackets
            # Championship SFs
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
                # Silver bracket (5th-8th)
                if max_grp_size >= 4:
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
                else:
                    po_games.append({'divName': div_name, 'lbl': '5th/6th', 'type': 'Medal',
                                     'bracket': 'Silver (5th–8th)', 'groups': group_letters,
                                     't1': '2nd Group A', 't2': '2nd Group B'})
                    po_games.append({'divName': div_name, 'lbl': '7th/8th', 'type': 'Medal',
                                     'bracket': 'Silver (5th–8th)', 'groups': group_letters,
                                     't1': '2nd Group C', 't2': '2nd Group D'})

                # Bronze bracket (9th-12th)
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
                # Placement bracket (13th-16th)
                po_games.append({'divName': div_name, 'lbl': '13th/14th', 'type': 'Medal',
                                 'bracket': '13th–16th Place', 'groups': group_letters,
                                 't1': '4th Group A', 't2': '4th Group B'})
                po_games.append({'divName': div_name, 'lbl': '15th/16th', 'type': 'Medal',
                                 'bracket': '13th–16th Place', 'groups': group_letters,
                                 't1': '4th Group C', 't2': '4th Group D'})

    return po_games


def _assemble_sched(rr_result, po_result, divisions, courts, day_slots, n_days, config):
    """Assemble the schedule in the format the frontend expects."""
    # Build gameDays
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

    # Build bracketDays (merged PO view) — games are SHARED references with
    # gameDays so the dedup in allSchedGames works correctly.
    bracket_div_map = {}
    for de in game_days:
        for d in de['divs']:
            gk = po_gk(d['name'])
            if gk in d.get('groups', {}):
                # Use the SAME game objects (not copies) so seen-Set dedup works
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
        'unscheduledGames': [],
        'sites': config.get('sites', []),
        'setupFields': config.get('setupFields', {}),
        'status': 'optimal',
        'stats': {
            'rr_games': len(rr_result),
            'po_games': len(po_result),
            'total': len(rr_result) + len(po_result),
        }
    }


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python scheduler.py config.json")
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
