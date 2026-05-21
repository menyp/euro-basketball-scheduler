# Issue #02 — PAX per team in setup → seat-aware shuttle-zone balance

**Status:** Backlog — not started
**Priority:** Medium (post-MVP enhancement)
**Estimated effort:** 6–10 hours

---

## Problem

The shuttle-zone balance soft-rule optimizes the **wrong quantity**. Today it
penalises the *count* of teams per zone at each away-venue slot:

> `penalty = (white_teams − black_teams)²` per (venue, day, slot)
> — `scheduler.py` `_solve_phase1` (~L1872–1922) and `_solve_phase2` (~L2553–2617)

But the real shuttle constraint is **seats (PAX)**, not team head-count. The
scheduler never even receives PAX — the `teamZones` config carries only
`white`/`black` per team (`_build_context`, ~L1351–1358), with no headcount. So
the solver literally cannot reason about bus load.

Consequence: a slot the solver considers "balanced" (e.g. 2 White + 2 Black
teams, penalty 0) can still overload one zone's 55-seat bus — e.g. White
VKKM (36) + MAN GIANTS (26) = 62 PAX vs Black 10 + 9 = 19. The Shuttle Risk
Register (separate project) then flags it RED, and team-swaps barely move the
needle because the solver keeps stacking large same-zone teams in one slot.

This was diagnosed against the 2026-05-21 dataset: after rebalancing zones
(U12 → 8W/5B, U16G → 7W/5B) the White zone still had 9 RED outbound slots,
driven by the solver being seat-blind plus a structurally larger White zone.

---

## Proposed Solution

1. **Add a PAX (passenger count) field per team in the Setup tab**, alongside
   the existing shuttle-zone (White/Black) assignment.
2. **Plumb PAX into the solver config** so each team carries `{div, team, zone,
   pax}` (extend the existing `teamZones` entries, or add a parallel `teamPax`
   array — whichever keeps `_buildSolverConfig` cleanest).
3. **Make the shuttle penalty seat-aware**: replace the count-based
   `(white_teams − black_teams)²` term with a **per-zone seat-capacity penalty**
   — penalise the PAX in each zone *above the bus capacity* per away-venue slot
   (configurable, default **55 seats/bus**). This directly targets the metric the
   Risk Register measures, so the solver prefers to spread large same-zone teams
   across slots and pair big-with-small.
4. **Backward compatible**: if PAX is missing for a team, fall back to the
   current count-based behaviour (treat each team as weight 1).

---

## Technical Design

- **UI (`index.html`)**: add a PAX input per team in the shuttle-zone setup
  area; default empty/0. Surface it next to the existing zone selector.
- **Config (`index.html` `_buildSolverConfig`)**: include PAX in the
  `teamZones` payload (or new `teamPax`).
- **Context (`scheduler.py` `_build_context`)**: parse PAX into a
  `team_pax[(div, team)]` map; default 0/None when absent.
- **Phase 1 / Phase 2 shuttle blocks** (`_solve_phase1` ~L1872–1922,
  `_solve_phase2` ~L2553–2617): swap the `(W−B)²` count term for a
  capacity-overflow penalty. Two viable formulations:
  - **(a) Overflow penalty:** `max(0, zonePAX − BUS_SEATS)²` per zone per slot
    (models "extra seats / second bus needed"). Most faithful to the register.
  - **(b) PAX-weighted diff:** `(whitePAX − blackPAX)²` — simpler, keeps the
    existing balance shape but seat-weighted.
  Recommend (a) since it matches the RED definition (load > 100% of one bus).
- **Bus capacity** should be a config value (default 55), not hard-coded, so it
  can track the real fleet.

---

## Acceptance Criteria

- [ ] Setup tab lets the admin enter PAX per team; persists in exported JSON.
- [ ] PAX flows through `_buildSolverConfig` → `_build_context` → both solve
      phases.
- [ ] Shuttle penalty is computed on PAX/seat-capacity, not team count.
- [ ] Bus seat capacity is configurable (default 55).
- [ ] Missing PAX falls back to count-based behaviour (no crash, no regression).
- [ ] On the 2026-05-21 dataset, away-venue White RED slots decrease vs the
      count-based solver (the solver spreads big White teams instead of stacking
      them).
- [ ] Existing pytest suite stays green; add tests for `_build_context` PAX
      parsing and the seat-capacity penalty wiring.

---

## Out of Scope

- The Shuttle Risk Register itself (separate `tournament-registration` project —
  it already has PAX; this issue is only about the *scheduler* being seat-aware).
- Physical fleet capacity. Even a seat-aware solver is bounded by the
  white-heavy zone pool + one bus per zone at each away venue; clearing the
  residual REDs still needs a 2nd White run / larger coach on peak slots. That
  is an operations decision, not a solver change.
- Auto-deriving PAX from any roster file — PAX is admin-entered here.
