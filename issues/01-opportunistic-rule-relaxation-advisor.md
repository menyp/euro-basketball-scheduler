# Issue #01 — Opportunistic Rule Relaxation Advisor

**Status:** Deferred — implement after production blockers fixed
**Priority:** Medium-High (post-MVP enhancement)
**Estimated effort:** 12–16 hours

---

## Problem

In tight tournament configurations (few courts, packed schedule), the scheduler can fail to place all games while strictly honoring every rule — even when relaxing **one specific rule** for **one specific division** would unlock multiple additional placements.

Currently the admin sees only *"18 games unscheduled — add more courts or extend hours"*. They have no way to know that, for example:

> *"If BOYS U18 played 1 of its 6 group games at Palafolls instead of Blanes, 4 additional games would fit in the schedule."*

Two bad options today:
1. **Strict (current)**: Accept the failures. Admin doesn't know a better schedule was possible.
2. **Silent relaxation (Pass 6, currently being fixed)**: The scheduler silently breaks the venue rule. Admin doesn't notice until the event.

Neither gives the admin the right trade-off to evaluate.

---

## Proposed Solution

Add an **"Opportunistic Rule Relaxation Advisor"** that:

1. Runs **only when there are unscheduled games** (zero overhead on happy paths)
2. Explores a small set of targeted **what-if simulations** (~5–10 candidates)
3. Identifies relaxations that unlock ≥2 additional placements
4. **Presents them to the admin** with clear before/after previews
5. Admin **approves or dismisses** each — no silent changes

---

## UX Flow

### Entry point
After scheduler finishes with unscheduled games, the existing red alert banner shows a new button:

```
⚠️  18 games could not be scheduled                                    [Show ▲]
    12 round-robin + 6 playoff · 3 divisions affected
    [💡 Show Scheduling Advisor (3 suggestions available)]
```

### Advisor modal
Clicking opens a modal with the top 3–5 suggestions ranked by impact:

```
┌────────────────────────────────────────────────────────────────┐
│  💡 Scheduling Advisor                                    [✕]  │
│                                                                 │
│  We found ways to fit more games by relaxing specific rules.   │
│  Each suggestion is independent — approve the ones you like.   │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Suggestion 1  ·  +5 games placed                        │  │
│  │                                                           │  │
│  │  Relax venue rule: BOYS U18 plays 1 game at Palafolls    │  │
│  │  (currently "Blanes only")                                │  │
│  │                                                           │  │
│  │  Affected games:                                          │  │
│  │    • BOYS U18 Group A — Team X vs Team Y (10:30, Court 2)│  │
│  │                                                           │  │
│  │  Unlocks placement for:                                   │  │
│  │    • GIRLS U14 Group A — Team W vs Team Z               │  │
│  │    • U12 MIXED Group B — Team P vs Team Q               │  │
│  │    • ... (3 more)                                         │  │
│  │                                                           │  │
│  │  [ 👁 Preview schedule ]  [ ✓ Approve ]  [ ✕ Dismiss ]   │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Suggestion 2  ·  +3 games placed                        │  │
│  │  Allow 1 team in GIRLS U14 to play 3 games on Day 2     │  │
│  │  (currently max 2/day)                                   │  │
│  │  ...                                                      │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  [ Approve All ]  [ Dismiss All ]  [ Cancel ]                  │
└────────────────────────────────────────────────────────────────┘
```

### Preview mode
Clicking "Preview" temporarily overlays the main schedule view with the proposed schedule:
- **Green highlight**: newly placed games
- **Amber highlight**: games moved to accommodate the relaxation
- **Red X removed**: games that are no longer unscheduled
- Top bar: `"Previewing Suggestion 1 — [ Apply ]  [ Back to current ]"`

### Audit trail
After approval, a small badge appears at the top of the Schedule tab:

```
📋 Schedule generated with 1 approved relaxation:
   • BOYS U18 plays 1 game at Palafolls (approved 2026-04-12 14:32)
```

This makes the trade-off visible at all times — no silent changes.

---

## Technical Design

### Candidate relaxations to explore

Ranked by severity (least severe first — preferred):

1. **Venue priority downgrade** — e.g., a Mandatory division downgrades one game to High Priority, or High Priority allows one game at any venue
2. **maxGPD + 1** — specific team plays one extra game on one specific day
3. **Venue change rest reduction** — 180 → 90 min for specific team transitions
4. **Team rest reduction** — 90 → 60 or 0 min for specific games (most sensitive, require strong justification)

Each candidate is **scoped to a specific division/team/game**, never global.

### Algorithm

```
function runAdvisor(baselineSched, unscheduledGames) {
  if (unscheduledGames.length === 0) return [];
  
  const candidates = enumerateCandidates(unscheduledGames);
  const suggestions = [];
  
  for (const candidate of candidates) {
    const cloned = cloneSchedulerState(baselineSched);
    applyRelaxation(cloned, candidate);
    const result = rerunFailedPlacements(cloned);
    
    if (result.unlocked >= 2) {
      suggestions.push({
        candidate,
        unlocked: result.unlocked,
        affectedGames: result.movedGames,
        newPlacements: result.newlyPlacedGames,
        severityScore: computeSeverity(candidate),
        previewState: cloned,
      });
    }
  }
  
  return suggestions
    .sort((a, b) => (b.unlocked * 10 - b.severityScore) - (a.unlocked * 10 - a.severityScore))
    .slice(0, 5);
}
```

### Performance targets

- Main scheduler: ~400ms
- Advisor: ≤ 2s total for 10 candidates (each what-if is ~200ms since it reuses most of the state)
- Run in `requestIdleCallback` or `setTimeout(fn, 0)` so the main UI isn't blocked
- Show a "🔎 Analyzing..." spinner in the alert banner while it runs

### State management

- `baselineSched` is saved immediately after main generation (deep clone)
- Each what-if uses a cloned state — the baseline is never modified
- "Preview" toggles between baseline and candidate schedules in the DOM
- "Approve" commits the candidate as the new live schedule and re-runs `renderSched()`

### Incremental placement (optimization)

Don't re-run all 9 scheduler phases for each what-if. Instead:
1. Start from the baseline state
2. Apply only the relaxation
3. Re-attempt placement for **only the failed games** via `tryPlacePO` / `recordPlacement`
4. Measure the delta

This keeps each what-if to ~100–200ms.

---

## Acceptance Criteria

- [ ] Advisor only runs when `unscheduledGames.length > 0`
- [ ] Advisor never modifies the baseline schedule automatically
- [ ] Admin must explicitly approve each relaxation
- [ ] Preview mode clearly shows before/after with visual diff
- [ ] Approved relaxations are displayed as a permanent badge on the Schedule tab
- [ ] Advisor completes within 2 seconds for a 200-game tournament
- [ ] All existing smoke tests continue to pass unchanged
- [ ] New test scenarios cover: no unscheduled games (advisor skipped), unscheduled games with viable relaxations (advisor returns suggestions), unscheduled games with no viable relaxations (advisor returns empty set)

---

## Out of Scope

- Persisting approvals across sessions (the whole app is single-session today)
- Auto-approving relaxations without admin confirmation
- Machine-learning or historical optimization
- Cross-tournament analytics
- Integration with external approval workflows (email, Slack, etc.)

---

## Implementation Plan (when we get to this)

**Phase 1 — Infrastructure (4 hours):**
- Add `cloneSchedulerState()` utility that deep-clones `grid`, state maps, item arrays
- Add `rerunFailedPlacements(state)` that attempts only unplaced games
- Add `computeSeverity(candidate)` scoring function

**Phase 2 — Advisor engine (4 hours):**
- Implement `enumerateCandidates(unscheduledGames)` for each relaxation type
- Implement `runAdvisor()` loop with the performance target
- Unit tests for at least 3 scenarios (all rule types)

**Phase 3 — UI (4 hours):**
- Add "Show Scheduling Advisor" button to the alert banner
- Build modal with suggestion cards
- Implement preview toggle and approval flow
- Add approval badge to Schedule tab header

**Phase 4 — Integration test (2 hours):**
- End-to-end test: tight 9-court scenario → advisor suggests relaxations → admin approves → more games placed
- Verify audit badge shows correct approvals

---

## Dependencies

This issue depends on completion of these earlier fixes:
- ✅ Step 5a chronology fix (done)
- 🔴 tryPlacePO state tracking (Problems 1/2/3) — must be fixed first so what-if simulations are correct
- 🔴 Pass 6 courtAllowed enforcement (Problem 4) — must be fixed first to prevent silent relaxation from interfering with explicit advisor flow
- 🟠 Efficiency fixes (_sfReserve, _5aMainMaxSi) — should be done first so the baseline is already optimal before the advisor considers relaxations

Once those are in, this issue is unblocked.
