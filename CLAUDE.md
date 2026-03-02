# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview
Single-file tournament scheduler web app for the Euro Youth Basketball Cup.
The entire app lives in `index.html` — no build tools, no frameworks, no npm.
Open `index.html` directly in a browser to run it. No server needed.

## Key Rules
- **Never split into multiple files** unless explicitly asked
- **Never add frameworks** (React, Vue, etc.) unless explicitly asked
- **Preserve all default sample data** (teams, venues, dates) unless told otherwise
- Keep the dark navy + orange (#FF6B00) color scheme
- All JS is wrapped in an IIFE `(function(){ ... }())` to avoid Cloudflare injection issues

## Architecture
- Single HTML file with embedded CSS and JS
- State lives in plain JS variables: `divisions[]`, `sites[]`, `sched{}`
- No localStorage — everything resets on page reload (intentional for POC)
- Logo embedded as base64 PNG (no external image URLs)

## Main Tabs (in order)
1. **Setup** — tournament info, paste teams, venues, number of days
2. **Groups** — review/edit groups before generating; calls `ensureManualGroups()` then `renderGroups()`
3. **Schedule** — round robin games rendered by day and division; score inputs
4. **Standings** — W/L/PF/PA table computed live from locked scores; auto-sorted
5. **Bracket** — finals slots with per-division datalist dropdowns; only visible once round robin is fully scored (`isRoundRobinComplete()`)

## Division and Groups Data Model
Each division object has:
- `name`, `color`, `teams[]` — base fields
- `manualGroups[][]` — set by `ensureManualGroups()` (chunks teams into groups of ≤4); users can rearrange teams between groups in the Groups tab before generating

When generating, pairings are built from `d.manualGroups` (not `d.teams` directly). Each game carries a `group` label like `"Boys U14 Group A"`.

## Schedule Data Structure (`sched`)
```
sched = {
  gameDays: [               // one entry per round-robin day
    {
      label: "Day 1 — Round Robin",
      divs: [
        {
          name, color,
          groups: {          // keyed by group label string
            "Boys U14 Group A": {
              group: "Boys U14 Group A",
              games: [{ time, t1, t2, court, score1, score2, locked }]
            }
          }
          // NOTE: bracket/finals divs use `games[]` directly (no `groups`)
        }
      ]
    }
  ],
  bracketDays: [            // always 1 entry (last day)
    { label: "Day N — Semifinals & Finals",
      divs: [{ name, color, teams[], games[{time,t1,t2,lbl,court,...}] }] }
  ],
  totalCourts: N
}
```
**Critical:** Any code iterating `sched` must handle both shapes: `d.games` (bracket) and `d.groups[gk].games` (round robin). See `computeStandings`, `validateRoundRobin`, `isRoundRobinComplete`, and `renderDayBlock` for the pattern.

## Scheduling Logic
- `buildRounds(pairs)` — splits matchups into rounds where no team appears twice (used internally)
- Main scheduling loop (inside the `genBtn` click handler) — slot-based greedy algorithm: iterates available court/time slots, picks the first unplaced matchup where both teams are free (120-min gap enforced) and neither team has hit `maxGPD` for that day
- `placeGames(matchups, ...)` — used only for the finals/bracket day (places labeled SF/FINAL slots)
- Round robin fills first `(nDays - 1)` days; if games overflow, extra days are added automatically (up to `MAX_EXTRA_DAYS = 10`)
- Last day is always semifinals + finals bracket
- Lunch break window is respected — no games placed during it

## Scoring and Bracket Flow
- Score inputs: `<input class="score-in score1|score2" data-day data-div data-gi data-gk>`
- Scores are written directly into `sched.gameDays[day].divs[div].groups[gk].games[gi]`
- Editing a locked score requires modal confirmation (sets `game.locked = false`)
- `isRoundRobinComplete()` — returns `false` if any non-`lbl` game lacks both scores; gates bracket rendering
- `seedBracketFromStandings()` — once complete, auto-fills the FINAL slot with rank-1 vs rank-2

## When Making Changes
- Test with the default 8 divisions × mixed team counts × 10 courts × 3 days config
- Verify no team plays twice on the same day
- For grouped divisions, game count per group = n*(n-1)/2 where n = group size (≤4)
- When touching game iteration, always handle both `d.games` and `d.groups[gk].games`
- Keep file size under 100KB
