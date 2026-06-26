# Contributing

Quick reference for contributors. The project structure rules are in
[CLAUDE.md](CLAUDE.md); this document focuses on the **testing methodology**
that protects every change.

---

## Testing methodology

Every change is gated by automated tests that run in CI on every push and PR.
There are three suites; each catches a different class of regression.

| Suite        | What it covers                                                                 | When it runs                       |
|--------------|--------------------------------------------------------------------------------|------------------------------------|
| `py-fast`    | Pure-Python unit tests for `validator.py`, `build_context.py` parsing, helpers | every push, every PR               |
| `py-slow`    | End-to-end CP-SAT solver runs + Flask `/api/generate` happy-path               | PRs + pushes to `main` (skipped on feature pushes) |
| `js`         | jsdom-driven unit + smoke tests of `index.html`                                | every push, every PR               |

Workflow file: [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

### Why these three?

The codebase has two production languages and they fail in different ways:

- **`scheduler.py` / `validator.py` / `app.py`** — CP-SAT model and Flask API.
  Bugs here cause infeasible schedules, wrong groupings, or HTTP 500s. Caught
  by `pytest` under `tests/python/`.
- **`index.html`** — the entire UI, render layer, score logic, Excel export,
  Shuttle Load Planner, and the JS-greedy fallback solver. Bugs here cause
  broken rendering, wrong standings, finals seeding errors, or shuttle-load
  miscalculation. Caught by `tests/unit-tests.js` (UNIT + INTG + SHUTTLE +
  SNAPSHOT blocks, ~80 assertions) and `tests/smoke-test.js` (8 full
  scenarios driving the in-page JS-greedy solver). The JS suite drives the
  page through jsdom and asserts against the in-memory `sched` directly —
  no mocking, what runs in the browser runs in the test.

If you change Python, expect `py-fast`/`py-slow` to flag the regression. If
you change `index.html`, expect `js` to flag it. If you change both (e.g. a
new constraint that the UI also has to render) both suites must stay green.

---

## Running the tests locally

### Python

```bash
pip install -r requirements-dev.txt
pytest -m "not slow"        # fast suite (~2 s)
pytest -m slow              # full CP-SAT + API suite (~30-60 s)
pytest                       # everything
```

Coverage report (production modules only — `app.py`, `scheduler.py`,
`validator.py` — configured in [`.coveragerc`](.coveragerc)):

```bash
pytest --cov=. --cov-report=term-missing
pytest --cov=. --cov-report=html    # browse htmlcov/index.html
```

### JavaScript

```bash
cd tests
npm ci                       # installs jsdom (lockfile committed)
npm test                     # runs unit-tests.js then smoke-test.js
npm run test:unit            # unit checks only (50 tests)
npm run test:smoke           # 8-scenario smoke run
```

Both JS suites load the live `index.html`, evaluate its IIFE inside a jsdom
window, then drive the in-page solver and assert against `sched` directly. No
mocking — what runs in the browser runs in the test.

---

## Marker conventions

- `@pytest.mark.slow` — anything that invokes the CP-SAT solver or hits a
  Flask route end-to-end. CI runs these only on PRs and on `main` so feature
  pushes stay fast.
- No marker = fast unit test. Runs everywhere.

`pytest.ini` defaults to `-m "not slow"` so a bare `pytest` invocation in a
dev loop stays under 5 seconds.

---

## Feature → test map

The matrix below pairs each user-visible feature with the tests that guard it.
When you change a feature, the matching tests must stay green; when you add
a feature, add a row here so future contributors know where its guard lives.

| Feature                                                | Tests                                                              |
|--------------------------------------------------------|--------------------------------------------------------------------|
| Round-robin completeness (n*(n-1)/2 per group)         | `test_validator.py::*round_robin*`, `unit-tests.js` INTG 8        |
| Self-play prevention (t1 ≠ t2)                         | `test_validator.py::test_self_play_is_caught`, `unit-tests.js` INTG 14 |
| Court double-book                                      | `test_validator.py::test_court_double_booking_is_caught`, `unit-tests.js` smoke scenarios |
| Team double-book                                       | `test_validator.py::test_team_double_booking_is_caught`, `unit-tests.js` INTG 15 |
| Max games per day (`maxGPD`)                           | `test_validator.py::test_max_games_per_day_is_caught`, smoke scenarios 5+6 |
| Rest 90 min same-venue (R6)                            | `test_validator.py::test_rest_too_close_same_venue_is_caught`, smoke scenario 1 |
| Rest 270 min cross-venue (R7)                          | `test_validator.py::test_rest_too_close_across_venues_is_caught`, smoke scenario 1 |
| Lunch-break exclusion (R8)                             | `test_validator.py::test_game_during_lunch_is_flagged_as_lunch_violation` |
| Venue blackouts                                        | `test_validator.py::test_game_in_blacked_out_window_is_caught`, smoke scenario 8 |
| Late team arrival                                      | `test_validator.py::test_team_playing_before_arrival_is_caught`    |
| Zone-team-venue blocks                                 | `test_validator.py::test_blocked_team_at_blocked_venue_is_caught` |
| Main-venue RR guarantee (Step 4b swap pass)            | `test_validator.py::test_team_with_no_main_venue_rr_is_caught`, smoke scenarios 1-3 |
| Mandatory division at specific venue                   | `test_validator.py::test_division_off_its_mandatory_venue_is_caught`, `test_solve_invariants.py`, `unit-tests.js` INTG 9 |
| Finals / 3rd at main venue                             | `unit-tests.js` INTG 11                                            |
| Chronological integrity (RR ends before PO starts)     | `test_validator.py::test_rr_after_playoff_is_caught`, `unit-tests.js` INTG 6 |
| Finals sequencing (SF before FINAL within a bracket)   | `unit-tests.js` INTG 7                                             |
| No duplicate RR matchups                               | `unit-tests.js` INTG 8                                             |
| Optimal chunking (`optimalChunkSize`)                  | `unit-tests.js` UNIT 1                                             |
| Time-slot generation w/ lunch exclusion                | `unit-tests.js` UNIT 2                                             |
| Country extraction + national mixing                   | `unit-tests.js` UNIT 3 + 4 + INTG 10                                |
| Time helpers `toM` / `toT`                             | `unit-tests.js` UNIT 5, `test_time_helpers.py`                     |
| Score propagation → standings                          | `unit-tests.js` INTG 12 + 13 + 16                                  |
| Playoff structure (3333 / 4333 / 4433 / 4444 / 2x5 / crossover) | `test_po_structure.py`                                     |
| Training-game pairing                                  | `test_training_games.py`                                           |
| Flask `/api/generate` + `/api/validate` + `/api/health` | `test_api.py`                                                     |
| **Shuttle Load Planner — OUT + RET per game**          | `unit-tests.js` SHUTTLE 1                                          |
| **Lunch-aware OUT dep adjustment (+10 min into lunch)**| `unit-tests.js` SHUTTLE 2                                          |
| **Lunch-aware RET dep adjustment (pulled back to lunch start)** | `unit-tests.js` SHUTTLE 3                                |
| **PO TBD rows in both zone sheets**                    | `unit-tests.js` SHUTTLE 4                                          |
| **Idle bus rows (suppressed when slot is scheduled)**  | `unit-tests.js` SHUTTLE 5                                          |
| **Per-day per-bus capacity matrix**                    | `unit-tests.js` SHUTTLE 6                                          |
| **Shuttle risk classification (GREEN / YELLOW / RED / NA)** | `unit-tests.js` SHUTTLE 7 + 8                                  |
| **`buildSnapshot` (JSON export) shape**                | `unit-tests.js` SNAPSHOT 0                                         |
| **XLSX day-header contract (first 10 columns)**        | `unit-tests.js` SNAPSHOT 1                                         |
| Team-zone / team-pax config parsing                    | `test_build_context.py::test_team_zone_parsing_filters_invalid`, `test_team_pax_parsing_filters_invalid` |
| Bus seat default + override                            | `test_build_context.py::test_bus_seats_default_and_override`       |
| Zone-venue block lowering into court set               | `test_build_context.py::test_zone_venue_block_lowers_into_court_set_and_exempts_venue` |

## Adding tests when you make changes

| You changed…                                                          | Add a test in…                                       |
|-----------------------------------------------------------------------|------------------------------------------------------|
| A constraint in `scheduler.py` (new rule, new conflict, new objective)| `tests/python/test_solve_invariants.py` (slow)       |
| A parser/validator rule in `validator.py`                             | `tests/python/test_validator.py` (fast)              |
| A new field on the config payload                                     | `tests/python/test_build_context.py` (fast)          |
| A Flask route or new API field                                        | `tests/python/test_api.py` (slow for 200s, fast for 4xx) |
| A render path, schedule data structure, or score logic in `index.html`| `tests/unit-tests.js` INTG block                     |
| A scheduling-rule change visible in the JS-greedy fallback            | a new scenario in `tests/smoke-test.js`              |
| Shuttle / Load Planner logic in `index.html`                          | `tests/unit-tests.js` SHUTTLE block                  |
| Excel export / import / header layout                                 | `tests/unit-tests.js` SNAPSHOT block                 |

If a regression slips through CI, the right reaction is **add the test that
would have caught it**, then fix the bug.

---

## CI guardrails for new commits

- All three jobs must be green before merging to `main`.
- The `py-slow` job is skipped on plain feature pushes, but runs on every
  PR — so PRs are the merge gate, not pushes.
- `coverage.xml` is uploaded as a workflow artifact from the `py-fast` job;
  download it from the run page to inspect line-by-line coverage.
- The JS suite uses `npm ci` against the committed `tests/package-lock.json`
  for reproducible installs.

---

## Conventions for new test files

- **Python**: place in `tests/python/`, name `test_*.py`, mark slow tests
  with `@pytest.mark.slow`. Use the fixtures in
  [tests/python/conftest.py](tests/python/conftest.py) and the helpers in
  [tests/python/helpers.py](tests/python/helpers.py) rather than reinventing
  config builders.
- **JavaScript**: add cases to the existing `unit-tests.js` or
  `smoke-test.js` rather than creating new files. Both are wired into
  `npm test`; standalone scripts won't run in CI.

---

## When a test is wrong, not the code

It happens. Fix the test in the same PR as the code change and explain in the
PR description **why** the old assertion was wrong. Don't `xfail` or comment
out an assertion silently — that turns a guard into a lie.
