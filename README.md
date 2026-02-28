# Euro Youth Basketball Cup — Tournament Scheduler

A lightweight, offline-ready tournament scheduling web app built for the **Euro Youth Basketball Cup** (Barcelona, Spain 🇪🇸).

Live demo: *(add your GitHub Pages URL here once deployed)*

---

## Features

- **Paste & parse** team lists by division (e.g. `Boys U12: Team1, Team2, Team3`)
- **Multi-venue support** — define venues with individual court counts
- **Smart round-robin scheduling** — each team plays max 1 game per day
- **All games fit within N days** — round robin spread across first (N-1) days, finals on the last day
- **Lunch break avoidance** — no games scheduled during the lunch window
- **Color-coded divisions** — easy to read at a glance
- **Bracket day** — semis & finals with smart dropdowns per division (type or pick from list)
- **Fully offline** — single HTML file, no server needed, works on any device

---

## Getting Started

No installation required. Just open `index.html` in any browser.

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/euro-basketball-scheduler.git
cd euro-basketball-scheduler

# Open in browser (macOS)
open index.html

# Open in browser (Windows)
start index.html
```

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

## Deploying to GitHub Pages

1. Push this repo to GitHub
2. Go to **Settings → Pages**
3. Set source to `main` branch, `/ (root)`
4. Your app will be live at `https://YOUR_USERNAME.github.io/euro-basketball-scheduler`

---

## Project Structure

```
euro-basketball-scheduler/
├── index.html      # The entire app — single self-contained file
├── README.md       # This file
└── .gitignore      # Standard web gitignore
```

---

## Built With

- Vanilla HTML, CSS, JavaScript — no frameworks, no dependencies
- Google Fonts (Montserrat + Open Sans)
- Designed for the [Euro Youth Basketball Cup](https://www.eurobasketballcup.com)

---

## License

MIT — free to use and modify.
