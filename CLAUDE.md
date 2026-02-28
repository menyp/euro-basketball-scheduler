# Claude Code Instructions — Euro Basketball Scheduler

## Project Overview
Single-file tournament scheduler web app for the Euro Youth Basketball Cup.
The entire app lives in `index.html` — no build tools, no frameworks, no npm.

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

## Main Sections
1. **Setup tab** — tournament info, paste teams, venues, number of days
2. **Schedule tab** — round robin games rendered by day and division
3. **Bracket tab** — finals slots with per-division datalist dropdowns

## Scheduling Logic
- `buildRounds(pairs)` — splits matchups into rounds where no team appears twice
- `placeGames(matchups, ...)` — assigns games to courts using earliest-available-court algorithm
- Round robin fills first `(nDays - 1)` days evenly
- Last day is always semifinals + finals bracket
- Lunch break window is respected — no games placed during it

## When Making Changes
- Test with the default 8 divisions × 5 teams × 10 courts × 3 days config
- Verify no team plays twice on the same day
- Verify game count = n*(n-1)/2 per division for round robin
- Keep file size under 100KB
