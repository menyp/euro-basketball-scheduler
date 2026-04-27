---
title: EYBC Scheduler
emoji: 🏀
colorFrom: yellow
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
short_description: Euro Youth Basketball Cup tournament scheduler
---

# Euro Youth Basketball Cup — Tournament Scheduler

A tournament scheduling web app built for the **Euro Youth Basketball Cup** (Barcelona, Spain 🇪🇸), powered by Google OR-Tools CP-SAT for mathematically optimal placement.

Hosted version: *(add your Hugging Face Space URL here once deployed)*

GitHub Pages preview (UI + lower-quality JS-greedy fallback only): https://menyp.github.io/euro-basketball-scheduler/

---

## Features

- **Paste & parse** team lists by division (e.g. `Boys U12: Team1, Team2, Team3`)
- **Multi-venue support** — define venues with individual court counts and per-day blackout windows
- **CP-SAT optimal scheduling** — Google OR-Tools constraint solver finds optimal placements respecting team rest, venue rules, max-games-per-day, etc.
- **Iterative two-phase model** — RR placement, then PO placement, with PO-blocks-PO feedback loop (up to 4 iterations)
- **Multi-venue Mandatory / High-Priority rules per division** — e.g. "U18 BOYS only at Blanes + Pineda"
- **Tiered Venue Exclusivity** — Final / 3rd / Semi-final venue rules with Mandatory or High-Priority modes
- **Excel export** — multi-sheet workbook matching the Blanes 2025 organisers' format
- **Live progress banner** — see solver iteration + phase while it runs
- **Color-coded divisions** — easy to read at a glance
- **JSON snapshot** save/load for tournament drafts

---

## Getting Started

### Option 1 — use the hosted version (recommended for non-technical users)
Open the public URL above. No install. Full CP-SAT solver quality.

### Option 2 — run locally (for developers)

```bash
git clone https://github.com/menyp/euro-basketball-scheduler.git
cd euro-basketball-scheduler

pip install -r requirements.txt

python app.py
# → opens http://localhost:5000
```

### Option 3 — UI-only preview (no Python)
Open `index.html` directly in a browser, or visit the GitHub Pages link above. The UI works, but `Generate Schedule` falls back to a lower-quality JS-only greedy algorithm because the CP-SAT solver requires the Python backend.

---

## Default Sample Data

The app loads with sample data pre-filled:

**8 Divisions, 40 Teams:**
- Boys U10, U12, U14, U18
- Girls U10, U12, U14, U18

**3 Venues, 10 Courts:**
- Blanes — 6 courts
- Santa Suzana — 2 courts
- Palafolls — 2 courts

**Tournament:** June 19–21, 2026 · 20-min games · Lunch 1–3 PM

---

## How to Use

1. **Setup tab** — adjust dates, game duration, lunch break, teams, venues and number of days
2. Click **"Generate Tournament Schedule"**
3. **Schedule tab** — view the full round-robin schedule, sorted by time per day
4. **Bracket tab** — fill in qualifiers for the finals day using the dropdown per division

---

## Deployment

### Hugging Face Spaces (free, no card — recommended for public hosting)

1. Sign in at https://huggingface.co with GitHub OAuth.
2. **+ New Space** → SDK: **Docker** → Hardware: **CPU basic (free)** → public.
3. Connect this GitHub repo (Settings → Linked GitHub repository) for auto-deploy on push.
4. The included `Dockerfile` + `app.py` are ready to go. First build takes ~5–10 min.

See `Issue #11` for the full step-by-step.

### GitHub Pages (UI only)

GitHub Pages serves the static `index.html` only — no Python backend, so it falls back to the JS-greedy algorithm. Useful as a UI preview but not for production tournament scheduling.

---

## Project Structure

```
euro-basketball-scheduler/
├── index.html       # Single-page UI (vanilla HTML/CSS/JS, no frameworks)
├── app.py           # Flask server — serves the UI + /api/generate endpoint
├── scheduler.py     # CP-SAT solver (Google OR-Tools)
├── requirements.txt # Python dependencies
├── Dockerfile       # HF Spaces / Cloud Run deploy
├── tests/           # Unit + smoke tests
└── README.md
```

---

## Built With

- **Frontend:** Vanilla HTML, CSS, JavaScript — no frameworks
- **Backend:** Python 3 + Flask (single file, ~70 lines)
- **Solver:** Google OR-Tools CP-SAT
- **Production WSGI:** Gunicorn
- **Designed for the** [Euro Youth Basketball Cup](https://www.eurobasketballcup.com)

---

## License

MIT — free to use and modify.
