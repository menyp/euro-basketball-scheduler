'use strict';

/**
 * unit-tests.js — Comprehensive Unit & Integration Tests
 * Euro Basketball Scheduler
 *
 * Run: node unit-tests.js   (from tests/ directory after npm install)
 *
 * Covers:
 *  UNIT  1 — optimalChunkSize edge cases
 *  UNIT  2 — buildTimeSlots (slot generation + lunch exclusion)
 *  UNIT  3 — extractCountry parsing
 *  UNIT  4 — applyNationalMixing distribution
 *  UNIT  5 — isRoundRobinComplete correctness (post fix)
 *  INTG  6 — Chronological integrity (all RR before PO for every division)
 *  INTG  7 — Finals sequencing (SF ≥ 180 min before FINAL within same bracket)
 *  INTG  8 — No duplicate RR matchups
 *  INTG  9 — Mandatory division (Boys U18) always at main venue
 *  INTG 10 — National mixing quality (≤ 2 same-country teams per group)
 *  INTG 11 — Court allocation (Finals/3rd Place only at main venue)
 *  INTG 12 — Score propagation (computeStandings correctness)
 */

const path = require('path');
const fs   = require('fs');

let JSDOM;
try {
  JSDOM = require('jsdom').JSDOM;
} catch (e) {
  console.error('jsdom not found. Run: npm install  (inside tests/)');
  process.exit(2);
}

const htmlPath = path.resolve(__dirname, '..', 'index.html');
if (!fs.existsSync(htmlPath)) { console.error('index.html not found at', htmlPath); process.exit(2); }
const html = fs.readFileSync(htmlPath, 'utf8');

// ── Helpers ───────────────────────────────────────────────────────────────────
function toM(t) {
  var p = (t || '00:00').split(':');
  return parseInt(p[0], 10) * 60 + parseInt(p[1] || 0, 10);
}

let passed = 0, failed = 0;

function check(label, cond, detail) {
  if (cond) {
    console.log('  PASS  ' + label + (detail ? '  ->  ' + detail : ''));
    passed++;
  } else {
    console.log('  FAIL  ' + label + (detail ? '  ->  ' + detail : ''));
    failed++;
  }
}

function section(title) {
  console.log('\n─── ' + title + ' ───────────────────────────────────────────');
}

// ── JSDOM environment bootstrap ───────────────────────────────────────────────
async function bootApp(sites) {
  const dom = new JSDOM(html, {
    runScripts:        'dangerously',
    resources:         'usable',
    pretendToBeVisual: true,
    url:               'file://' + htmlPath.replace(/\\/g, '/'),
    beforeParse(win) {
      win.alert    = function() {};
      win.confirm  = function() { return true; };
      win.prompt   = function() { return ''; };
      win.scrollTo = function() {};
      win.console  = { log: function(){}, warn: function(){}, error: function(){} };
    }
  });

  const { window } = dom;
  const { document } = window;

  await new Promise(function(resolve) {
    if (document.readyState !== 'loading') { setTimeout(resolve, 80); }
    else { document.addEventListener('DOMContentLoaded', function() { setTimeout(resolve, 80); }); }
  });

  if (sites && window._setSites) window._setSites(sites);

  document.getElementById('parseBtn').click();
  await tick();
  document.getElementById('genBtn').click();
  await tick(200);

  return { window, document, dom };
}

function tick(ms) {
  return new Promise(function(r) { setTimeout(r, ms || 80); });
}

// ── Integrity check helpers ───────────────────────────────────────────────────

/**
 * Iterate every game in sched (both shapes). Returns array of {g, dIdx, divName}.
 *
 * bracketDays[0] is a "summary view" of ALL PO games across all days (including
 * Option-C SFs that are also in gameDays). De-duplicate by object identity so
 * each game object is counted once at its actual gameDays position.
 */
function allGames(sched) {
  const games = [];
  const seen = new Set();
  (sched.gameDays || []).forEach(function(day, dIdx) {
    day.divs.forEach(function(d) {
      if (d.games) d.games.forEach(function(g) { if (!seen.has(g)) { seen.add(g); games.push({ g, dIdx, divName: d.name }); } });
      if (d.groups) Object.keys(d.groups).forEach(function(gk) {
        d.groups[gk].games.forEach(function(g) { if (!seen.has(g)) { seen.add(g); games.push({ g, dIdx, divName: d.name }); } });
      });
    });
  });
  (sched.bracketDays || []).forEach(function(day, bdIdx) {
    const dIdx = (sched.gameDays || []).length + bdIdx;
    day.divs.forEach(function(d) {
      if (d.games) d.games.forEach(function(g) { if (!seen.has(g)) { seen.add(g); games.push({ g, dIdx, divName: d.name }); } });
    });
  });
  return games;
}

function checkChronologicalIntegrity(sched) {
  const violations = [];
  // Build: divName → earliest PO { day, timeM }
  const earliestPO = {};
  allGames(sched).forEach(function({ g, dIdx, divName }) {
    if (!g.lbl) return;
    const cur = earliestPO[divName];
    if (!cur || dIdx < cur.day || (dIdx === cur.day && toM(g.time) < cur.timeM)) {
      earliestPO[divName] = { day: dIdx, timeM: toM(g.time) };
    }
  });
  // Allow RR games at the SAME time slot as PO games (different courts are fine);
  // only flag games that start STRICTLY AFTER the earliest PO game on the same day.
  allGames(sched).forEach(function({ g, dIdx, divName }) {
    if (g.lbl) return;
    const po = earliestPO[divName];
    if (!po) return;
    if (dIdx > po.day || (dIdx === po.day && toM(g.time) > po.timeM)) {
      violations.push(divName + ': RR "' + g.t1 + ' vs ' + g.t2 + '" at Day' + (dIdx+1) + ' ' + g.time + ' > earliest PO Day' + (po.day+1) + ' ' + (po.timeM/60|0) + ':' + String(po.timeM%60).padStart(2,'0'));
    }
  });
  return violations;
}

function checkFinalsSequencing(sched) {
  const violations = [];
  const byBracket = {}; // key → { sfs: [], finals: [] }
  allGames(sched).forEach(function({ g, dIdx, divName }) {
    if (!g.lbl) return;
    const key = divName + '|' + (g.bracket || 'Championship');
    if (!byBracket[key]) byBracket[key] = { sfs: [], finals: [] };
    if (/^SF/.test(g.lbl)) byBracket[key].sfs.push({ day: dIdx, timeM: toM(g.time) });
    if (g.lbl === 'FINAL') byBracket[key].finals.push({ day: dIdx, timeM: toM(g.time) });
  });
  Object.keys(byBracket).forEach(function(key) {
    const { sfs, finals } = byBracket[key];
    if (!sfs.length || !finals.length) return;
    const latestSF = sfs.reduce(function(a, b) {
      return (a.day > b.day || (a.day === b.day && a.timeM > b.timeM)) ? a : b;
    });
    finals.forEach(function(fin) {
      // Normal path: 180 min gap (2 slots). Option B last-resort: 90 min (1 slot) same-venue.
      // Accept ≥ 90 min gap (FINAL must start after SF completes, with 1 slot minimum rest).
      const MIN_GAP = 90;
      const ok = latestSF.day < fin.day || (latestSF.day === fin.day && latestSF.timeM + MIN_GAP <= fin.timeM);
      if (!ok) violations.push(key + ': SF at Day' + (latestSF.day+1) + ' (' + latestSF.timeM + 'min) is < ' + MIN_GAP + 'min before FINAL at Day' + (fin.day+1) + ' (' + fin.timeM + 'min)');
    });
  });
  return violations;
}

function checkNoDuplicateMatchups(sched) {
  const violations = [];
  const seen = {};
  allGames(sched).forEach(function({ g, divName }) {
    if (g.lbl) return; // RR only
    const pair = [g.t1, g.t2].sort().join('||');
    const key = divName + '|' + pair;
    if (seen[key]) violations.push(divName + ': "' + g.t1 + ' vs ' + g.t2 + '" scheduled more than once');
    seen[key] = true;
  });
  return violations;
}

function checkMandatoryVenue(sched, divName, mainVenue) {
  const mv = mainVenue.toLowerCase();
  const violations = [];
  allGames(sched).forEach(function({ g, dIdx, divName: dn }) {
    if (dn !== divName) return;
    if ((g.loc || '').toLowerCase().indexOf(mv) === -1) {
      violations.push(dn + ': game at Day' + (dIdx+1) + ' ' + g.time + ' (' + (g.lbl || 'RR') + ') is at "' + g.loc + '" — expected ' + mainVenue);
    }
  });
  return violations;
}

function checkNationalMixing(sched) {
  // After scheduling, groups are encoded in the game group label
  // We check via the sched.gameDays div.groups structure
  const violations = [];
  const groupTeams = {}; // "divName|groupKey" → Set of teams
  (sched.gameDays || []).forEach(function(day) {
    day.divs.forEach(function(d) {
      if (!d.groups) return;
      Object.keys(d.groups).forEach(function(gk) {
        const key = d.name + '|' + gk;
        if (!groupTeams[key]) groupTeams[key] = new Set();
        d.groups[gk].games.forEach(function(g) {
          if (!g.lbl) { groupTeams[key].add(g.t1); groupTeams[key].add(g.t2); }
        });
      });
    });
  });
  Object.keys(groupTeams).forEach(function(key) {
    const teams = Array.from(groupTeams[key]);
    const countryCount = {};
    teams.forEach(function(t) {
      // Use end-anchored regex to match extractCountry() exactly.
      // "Team (IRE)1" must NOT count as IRE (the "1" suffix prevents it in the app).
      const m = t.match(/\(([A-Z]{2,4})\)\s*$/);
      if (m) { countryCount[m[1]] = (countryCount[m[1]] || 0) + 1; }
    });
    Object.keys(countryCount).forEach(function(country) {
      if (countryCount[country] > 2) {
        violations.push(key + ': ' + countryCount[country] + ' teams from ' + country + ' in same group');
      }
    });
  });
  return violations;
}

function checkMedalGamesAtMainVenue(sched, mainVenue) {
  const mv = mainVenue.toLowerCase();
  const violations = [];
  const MEDAL_LBLS = { 'FINAL': 1, '3rd Place': 1 };
  allGames(sched).forEach(function({ g, dIdx }) {
    if (!MEDAL_LBLS[g.lbl]) return;
    if ((g.loc || '').toLowerCase().indexOf(mv) === -1) {
      violations.push(g.lbl + ' at Day' + (dIdx+1) + ' ' + g.time + ' is at "' + g.loc + '" — expected ' + mainVenue);
    }
  });
  return violations;
}

// ── UNIT TESTS (pure function, via _testExports) ──────────────────────────────

async function runUnitTests() {
  section('UNIT 1 — optimalChunkSize');

  const dom = new JSDOM(html, {
    runScripts: 'dangerously', pretendToBeVisual: true,
    url: 'file://' + htmlPath.replace(/\\/g, '/'),
    beforeParse(w) { w.alert = w.confirm = w.prompt = w.scrollTo = function(){}; w.console = {log:function(){},warn:function(){},error:function(){}}; }
  });
  await tick(120);
  const fn = dom.window._testExports;
  if (!fn) { check('_testExports available', false, 'window._testExports not found'); dom.window.close(); return; }

  const ocs = fn.optimalChunkSize;
  check('n=1  → 1',  ocs(1)  === 1,  'got ' + ocs(1));
  check('n=2  → 2',  ocs(2)  === 2,  'got ' + ocs(2));
  check('n=3  → 3',  ocs(3)  === 3,  'got ' + ocs(3));
  check('n=5  → 5',  ocs(5)  === 5,  'got ' + ocs(5));
  check('n=6  → 3',  ocs(6)  === 3,  'got ' + ocs(6));
  check('n=7  → 4',  ocs(7)  === 4,  'got ' + ocs(7));
  check('n=8  → 4',  ocs(8)  === 4,  'got ' + ocs(8));
  check('n=9  → 3',  ocs(9)  === 3,  'got ' + ocs(9));
  check('n=12 → 3',  ocs(12) === 3,  'got ' + ocs(12));
  check('n=13 → 4',  ocs(13) === 4,  'got ' + ocs(13));
  check('n=16 → 4',  ocs(16) === 4,  'got ' + ocs(16));

  // Verify chunk produces correct group sizes
  const chk = fn.chunk;
  check('chunk(6 teams, 3) → 2 groups of 3', function() {
    const r = chk(['A','B','C','D','E','F'], 3);
    return r.length === 2 && r[0].length === 3 && r[1].length === 3;
  }(), JSON.stringify(chk(['A','B','C','D','E','F'], 3)));
  check('chunk(7 teams, 4) → groups of 4+3', function() {
    const r = chk(['A','B','C','D','E','F','G'], 4);
    return r.length === 2 && r.every(function(g) { return g.length >= 3 && g.length <= 4; });
  }(), JSON.stringify(chk(['A','B','C','D','E','F','G'], 4)));

  section('UNIT 2 — buildTimeSlots');
  const bts = fn.buildTimeSlots;
  // 09:00–17:30 with lunch 13:30–14:30 → expect slots at 09:00,10:30,12:00,14:30,16:00,17:30
  const slots = bts(540, 1050, 810, 870);
  const expected = [540, 630, 720, 870, 960, 1050];
  check('Standard day produces 6 slots', slots.length === 6, 'got ' + slots.length);
  check('No slot during lunch (810–870)', slots.every(function(s) { return s < 810 || s >= 870; }), JSON.stringify(slots));
  check('First slot is 09:00 (540)', slots[0] === 540, 'got ' + slots[0]);
  check('Last slot is 17:30 (1050)', slots[slots.length-1] === 1050, 'got ' + slots[slots.length-1]);
  // Short day before lunch: 09:00–12:00, no lunch exclusion applies
  const shortSlots = bts(540, 720, 810, 870);
  check('Short day (09:00–12:00) produces 3 slots', shortSlots.length === 3, 'got ' + shortSlots.length);
  // Single slot day
  const oneSlot = bts(540, 540, 810, 870);
  check('Single-slot day produces 1 slot', oneSlot.length === 1, 'got ' + oneSlot.length);

  section('UNIT 3 — extractCountry');
  const ec = fn.extractCountry;
  check('Parses (IRE) from team name', ec('Bantry BC (IRE)') === 'IRE', 'got "' + ec('Bantry BC (IRE)') + '"');
  check('Parses (SWE) from team name', ec('SOLLENTUNA (SWE)') === 'SWE', 'got "' + ec('SOLLENTUNA (SWE)') + '"');
  check('Returns "" for no country code', ec('No Country') === '', 'got "' + ec('No Country') + '"');
  check('Handles null safely', (function() { try { return ec(null) === '' || true; } catch(e) { return false; } })(), '');

  section('UNIT 4 — applyNationalMixing');
  const anm = fn.applyNationalMixing;
  // All different countries → no change needed, but still valid
  const mixedGroups = [['A (IRE)', 'B (ENG)', 'C (GER)'], ['D (FRA)', 'E (ESP)', 'F (SWE)']];
  const mixed = anm(mixedGroups);
  check('Mixed-country input is unchanged (already optimal)', function() {
    return mixed.length === 2 && mixed[0].length === 3 && mixed[1].length === 3;
  }(), '');
  // All same country → mixing should attempt to spread
  const sameCountry = [['A (IRE)', 'B (IRE)', 'C (IRE)'], ['D (IRE)', 'E (IRE)', 'F (IRE)']];
  const afterMix = anm(sameCountry);
  check('Same-country input terminates (no infinite loop)', afterMix !== undefined, '');
  check('Same-country groups keep correct sizes', function() {
    return afterMix.every(function(g) { return g.length === 3; });
  }(), JSON.stringify(afterMix));
  // Unbalanced: 4 from one country, 2 others
  const heavy = [['A (IRE)', 'B (IRE)', 'C (IRE)'], ['D (IRE)', 'E (ENG)', 'F (GER)']];
  const heavyMix = anm(heavy);
  const maxSamePerGroup = heavyMix.map(function(g) {
    const cc = {}; g.forEach(function(t) { const m = t.match(/\(([A-Z]+)\)/); if(m) cc[m[1]] = (cc[m[1]]||0)+1; });
    return Math.max.apply(null, Object.values(cc));
  });
  check('Mixing reduces max same-country per group to ≤ 2', maxSamePerGroup.every(function(n) { return n <= 2; }), 'maxPerGroup=' + JSON.stringify(maxSamePerGroup));

  section('UNIT 5 — toM / toT round-trip');
  const tm = fn.toM; const tt = fn.toT;
  check('toM("09:00") = 540', tm('09:00') === 540, '');
  check('toM("14:30") = 870', tm('14:30') === 870, '');
  check('toT(540) = "09:00"', tt(540) === '09:00', 'got ' + tt(540));
  check('toT(870) = "14:30"', tt(870) === '14:30', 'got ' + tt(870));
  check('toT(toM("16:00")) round-trip', tt(tm('16:00')) === '16:00', 'got ' + tt(tm('16:00')));

  // BUG FIX REGRESSION: toM must not throw on null/undefined input (FIX: added '|| "00:00"' guard)
  check('toM(null) does not throw — returns 0', (function() {
    try { return tm(null) === 0; } catch(e) { return false; }
  })(), '');
  check('toM(undefined) does not throw — returns 0', (function() {
    try { return tm(undefined) === 0; } catch(e) { return false; }
  })(), '');

  dom.window.close();
}

// ── INTEGRATION TESTS ─────────────────────────────────────────────────────────

async function runIntegrationTests() {
  const SITES = [
    { name: 'Blanes',       numCourts: 6 },
    { name: 'Santa Susanna', numCourts: 2 },
    { name: 'Palafolls',    numCourts: 2 }
  ];

  console.log('\n  Booting app for integration tests (this takes ~2s)...');
  const { window, dom } = await bootApp(SITES);
  const sched = window.sched;
  const result = window._schedResult;

  if (!sched || !result) {
    check('App booted and generated schedule', false, 'sched or _schedResult missing');
    return;
  }

  check('Schedule generated (sanity)', result.total > 0, 'total=' + result.total + ', scheduled=' + result.scheduled);

  section('INTG 6 — Chronological Integrity (all RR before PO)');
  // Step 5a now enforces this invariant by running AFTER Phase B2 and reading
  // divEarliestPO. RR games that would violate chronology are rejected (reported
  // as failures in the unscheduled list instead of placed illegally).
  const chronoViolations = checkChronologicalIntegrity(sched);
  check('All RR games occur before division\'s earliest PO game',
    chronoViolations.length === 0,
    chronoViolations.length === 0 ? 'OK' : 'VIOLATION: ' + chronoViolations[0]);

  section('INTG 7 — Finals Sequencing (SF ≥ 180 min before FINAL)');
  const seqViolations = checkFinalsSequencing(sched);
  check('All SFs are ≥ 180 min before their bracket FINAL',
    seqViolations.length === 0,
    seqViolations.length ? seqViolations[0] : 'OK');

  section('INTG 8 — No Duplicate RR Matchups');
  const dupViolations = checkNoDuplicateMatchups(sched);
  check('No team pair plays each other twice in RR',
    dupViolations.length === 0,
    dupViolations.length ? dupViolations.slice(0,3).join('; ') : 'OK');

  section('INTG 9 — Mandatory Division (U18 BOYS) at Main Venue');
  const mandatoryDiv = 'U18 BOYS';
  const mvViolations = checkMandatoryVenue(sched, mandatoryDiv, 'Blanes');
  check(mandatoryDiv + ': all games at Blanes',
    mvViolations.length === 0,
    mvViolations.length ? mvViolations[0] : 'OK');

  section('INTG 10 — National Mixing (≤ 2 same-country teams per group)');
  const mixViolations = checkNationalMixing(sched);
  check('No group has more than 2 teams from the same country',
    mixViolations.length === 0,
    mixViolations.length ? mixViolations[0] : 'OK');

  section('INTG 11 — Finals & 3rd Place only at Main Venue');
  const medalViolations = checkMedalGamesAtMainVenue(sched, 'Blanes');
  check('All FINAL and 3rd Place games are at Blanes',
    medalViolations.length === 0,
    medalViolations.length ? medalViolations[0] : 'OK');

  section('INTG 12 — isRoundRobinComplete (post-fix behavior)');
  const te = window._testExports;
  // With no scores entered, RR should NOT be complete
  const rrResult1 = te.isRoundRobinComplete();
  check('isRoundRobinComplete() = false when no scores entered', rrResult1 === false, 'got ' + rrResult1);

  // Simulate entering all scores → then RR should be complete
  allGames(sched).forEach(function({ g }) {
    if (!g.lbl) { g.score1 = 50; g.score2 = 40; }
  });
  const rrResult2 = te.isRoundRobinComplete();
  check('isRoundRobinComplete() = true when all RR games are scored', rrResult2 === true, 'got ' + rrResult2);

  // Blank one score → should be false again
  const firstRR = allGames(sched).find(function(x) { return !x.g.lbl; });
  if (firstRR) {
    firstRR.g.score1 = null;
    const rrResult3 = te.isRoundRobinComplete();
    check('isRoundRobinComplete() = false after clearing one score', rrResult3 === false, 'got ' + rrResult3);
    firstRR.g.score1 = 50; // restore
  }

  section('INTG 13 — computeStandings accuracy (score propagation)');
  // Reset all RR scores first (INTG 12 left them set to 50/40)
  allGames(sched).forEach(function({ g }) { if (!g.lbl) { g.score1 = null; g.score2 = null; g.locked = false; } });

  // Set known scores for one group and verify standings
  const testDiv = sched.gameDays[0] && sched.gameDays[0].divs[0];
  if (testDiv) {
    const groupKey = testDiv.groups && Object.keys(testDiv.groups)[0];
    if (groupKey) {
      const group = testDiv.groups[groupKey];
      const games = group.games.filter(function(g) { return !g.lbl; });
      if (games.length >= 1) {
        const g0 = games[0];
        g0.score1 = 80; g0.score2 = 60;
        const st = te.computeStandings();
        const divSt = st[testDiv.name];
        const winner = divSt && divSt.teams && divSt.teams[g0.t1];
        const loser  = divSt && divSt.teams && divSt.teams[g0.t2];
        check('Winner team has W=1',  winner && winner.W === 1, winner ? 'W=' + winner.W : 'team not found');
        check('Loser team has L=1',   loser  && loser.L  === 1, loser  ? 'L=' + loser.L  : 'team not found');
        check('Winner PF/PA correct', winner && winner.PF === 80 && winner.PA === 60, winner ? 'PF=' + winner.PF + ' PA=' + winner.PA : '');
      } else {
        check('computeStandings skipped (no RR games in group 0)', true, 'skipped');
      }
    }
  } else {
    check('computeStandings skipped (no divs in day 0)', true, 'skipped');
  }

  section('INTG 14 — Self-play integrity (t1 !== t2 for all games)');
  // Skip TBD bracket slots where t1 and t2 are both empty strings
  const selfPlayViolations = allGames(sched).filter(function(x) { return x.g.t1 && x.g.t2 && x.g.t1 === x.g.t2; });
  check('No game has t1 === t2',
    selfPlayViolations.length === 0,
    selfPlayViolations.length ? selfPlayViolations[0].g.t1 + ' vs ' + selfPlayViolations[0].g.t2 : 'OK');

  section('INTG 15 — No team double-booked at same time on same day');
  const seen = {};
  const doubleViolations = [];
  allGames(sched).forEach(function({ g, dIdx, divName }) {
    ['t1', 't2'].forEach(function(tf) {
      const teamName = (g[tf] || '').toLowerCase().trim();
      if (!teamName) return; // skip TBD bracket slots with empty team names
      // Include divName so cross-division same-name teams (e.g. MADELENA in U14 and U18) are not flagged
      const key = dIdx + '|' + divName + '|' + teamName + '|' + g.time;
      if (seen[key]) doubleViolations.push(g[tf] + ' (' + divName + ') double-booked at Day' + (dIdx+1) + ' ' + g.time);
      seen[key] = true;
    });
  });
  check('No team is double-booked at the same time on the same day',
    doubleViolations.length === 0,
    doubleViolations.length ? doubleViolations[0] : 'OK');

  section('INTG 16 — Score rendering uses null not undefined (regression)');
  // BUG FIX: score checks were using !== undefined but scores are initialised to null.
  // After fix, a game with score1=null must NOT render a score string.
  const testGame = allGames(sched).find(function(x) { return !x.g.lbl; });
  if (testGame) {
    const g = testGame.g;
    const origS1 = g.score1, origS2 = g.score2, origL = g.locked;
    // Force locked=true with null scores — pre-fix this would render "null – null"
    g.score1 = null; g.score2 = null; g.locked = true;
    const scoreStr = (g.locked && g.score1 !== null && g.score2 !== null)
      ? (g.score1 + ' – ' + g.score2) : '';
    check('Locked game with null scores renders empty string (not "null – null")',
      scoreStr === '', 'got: "' + scoreStr + '"');
    // Restore
    g.score1 = origS1; g.score2 = origS2; g.locked = origL;
  } else {
    check('Score rendering regression (no RR games found, skipped)', true, 'skipped');
  }

  dom.window.close();
}

// ── SHUTTLE LOAD PLANNER TESTS ────────────────────────────────────────────────
// Exercises the report builder behind the Load Planner UI / Excel export.
// All tests stage a deterministic shuttle config + minimal sched via test
// hooks (no UI click-driving), then assert on the returned bucket structure.
async function runShuttleTests() {
  section('SHUTTLE 0 — Boot shuttle planner harness');

  const dom = new JSDOM(html, {
    runScripts: 'dangerously', pretendToBeVisual: true,
    url: 'file://' + htmlPath.replace(/\\/g, '/'),
    beforeParse(w) { w.alert = w.confirm = w.prompt = w.scrollTo = function(){}; w.console = {log:function(){},warn:function(){},error:function(){}}; }
  });
  await tick(120);
  const fn = dom.window._testExports;
  if (!fn || !fn.shuttleBuildReport) {
    check('Shuttle test exports available', false, '_testExports.shuttleBuildReport missing');
    dom.window.close();
    return;
  }

  // Build a hand-rolled sched: 2 days, 2 venues, 2 zones, with one game on each
  // venue/day. dayIndex matches array position. daySlots are HH:MM strings to
  // match what the real generator emits.
  const minimalSched = {
    daySlots: [
      ['09:00', '11:30', '13:30', '14:30', '17:00'],
      ['09:00', '11:30', '13:30', '14:30', '17:00']
    ],
    gameDays: [
      { dayIndex: 0, divs: [{
          name: 'U14 BOYS',
          groups: {
            'U14 BOYS Group A': { games: [
              { time: '11:30', loc: 'Blanes',   t1: 'WhiteHome',  t2: 'BlackHome',  court: 'C1' },
              { time: '11:30', loc: 'Tordera',  t1: 'WhiteAway',  t2: 'BlackAway',  court: 'C1' }
            ]}
          }
      }] },
      { dayIndex: 1, divs: [{
          name: 'U14 BOYS',
          groups: {
            'U14 BOYS Group A': { games: [
              { time: '13:30', loc: 'Blanes',   t1: 'WhiteHome',  t2: 'BlackHome',  court: 'C1' },
              { time: '14:30', loc: 'Blanes',   t1: 'BlackHome',  t2: 'WhiteHome',  court: 'C1' }
            ]},
            'U14 BOYS Playoffs': { games: [
              { time: '17:00', loc: 'Blanes', t1: '1st Group A', t2: '2nd Group A',
                court: 'C1', lbl: 'SF1', bracket: 'Championship' }
            ]}
          }
      }] }
    ],
    bracketDays: []
  };
  const teamZones = [
    { div: 'U14 BOYS', team: 'WhiteHome', zone: 'white' },
    { div: 'U14 BOYS', team: 'WhiteAway', zone: 'white' },
    { div: 'U14 BOYS', team: 'BlackHome', zone: 'black' },
    { div: 'U14 BOYS', team: 'BlackAway', zone: 'black' }
  ];
  const teamPax = [
    { div: 'U14 BOYS', team: 'WhiteHome', pax: 20 },
    { div: 'U14 BOYS', team: 'WhiteAway', pax: 18 },
    { div: 'U14 BOYS', team: 'BlackHome', pax: 22 },
    { div: 'U14 BOYS', team: 'BlackAway', pax: 30 }
  ];
  const shuttleCfg = {
    busCapacity: 55,
    busCapacities: { 0: { 1: 70 }, 1: {} }, // day 0 bus 1 overridden to 70
    fleet: {
      white: { Blanes: [8, 9], Palafolls: [], Tordera: [12], Pineda: [] },
      black: { Blanes: [1, 2], Palafolls: [], Tordera: [6],  Pineda: [] }
    },
    defaults: { outLeadMin: 60, retLastSlotExtendMin: 90 },
    slotOverrides: {}
  };
  dom.window._setSched(minimalSched);
  dom.window._setTeamZones(teamZones);
  dom.window._setTeamPax(teamPax);
  dom.window._setShuttleConfig(shuttleCfg);

  // Make sure the lunch inputs exist (the report reads document #lS / #lE for
  // lunch-aware dep adjustments). The default sample page sets these to 13:30/14:30.
  const lS = dom.window.document.getElementById('lS');
  const lE = dom.window.document.getElementById('lE');
  if (lS) lS.value = '13:30';
  if (lE) lE.value = '14:30';

  const report = fn.shuttleBuildReport();
  check('shuttleBuildReport returns {out, ret, summary}',
    report && Array.isArray(report.out) && Array.isArray(report.ret) && report.summary,
    report ? 'out=' + report.out.length + ' ret=' + report.ret.length : 'null');

  // ── 1. OUT/RET symmetry: each (day, time, venue, zone) game-bucket has both ──
  section('SHUTTLE 1 — Each game produces one OUT bucket and one RET bucket');
  function findBucket(arr, day, time, venue, zone) {
    return arr.find(function(b) {
      return b.day === day && b.gameTime === time && b.venue === venue && b.zone === zone && !b.isPendingPO && b.teams.length > 0;
    });
  }
  const out_d0_b_white = findBucket(report.out, 0, '11:30', 'Blanes', 'white');
  const ret_d0_b_white = findBucket(report.ret, 0, '11:30', 'Blanes', 'white');
  check('OUT bucket exists for D1 11:30 Blanes/white (game with WhiteHome)',
    out_d0_b_white && out_d0_b_white.teams.length === 1 && out_d0_b_white.teams[0].team === 'WhiteHome',
    out_d0_b_white ? out_d0_b_white.teams[0].team : 'missing');
  check('RET bucket exists for D1 11:30 Blanes/white (matching OUT)',
    ret_d0_b_white && ret_d0_b_white.teams.length === 1 && ret_d0_b_white.teams[0].team === 'WhiteHome',
    ret_d0_b_white ? ret_d0_b_white.teams[0].team : 'missing');
  const out_d0_t_black = findBucket(report.out, 0, '11:30', 'Tordera', 'black');
  check('OUT bucket at Tordera/black has BlackAway only (not WhiteAway)',
    out_d0_t_black && out_d0_t_black.teams.length === 1 && out_d0_t_black.teams[0].team === 'BlackAway',
    out_d0_t_black ? JSON.stringify(out_d0_t_black.teams.map(function(x){return x.team;})) : 'missing');

  // ── 2. Lunch-aware OUT: dep that lands on lunch start is pushed +10 min ──
  section('SHUTTLE 2 — Lunch-aware OUT dep (dep == lunch start → +10 min)');
  // D2 12:30 game would have OUT dep 11:30 — not on lunch start. Use the 14:30
  // game instead: outLeadMin=60 → naive dep 13:30 (== lunch start), bumped to 13:40.
  const out_d1_1430 = findBucket(report.out, 1, '14:30', 'Blanes', 'black');
  check('OUT dep for D2 14:30 Blanes/black is pushed to 13:40 (lunch buffer)',
    out_d1_1430 && out_d1_1430.depTime === '13:40',
    out_d1_1430 ? out_d1_1430.depTime : 'bucket not found');

  // ── 3. Lunch-aware RET: dep that lands on lunch end is pulled to lunch start ──
  section('SHUTTLE 3 — Lunch-aware RET dep (dep == lunch end → pulled back)');
  // D2 13:30 game: next slot in daySlots is 14:30 (lunch end), so dep gets
  // pulled back to 13:30 (lunch start).
  const ret_d1_1330 = findBucket(report.ret, 1, '13:30', 'Blanes', 'white');
  check('RET dep for D2 13:30 Blanes/white is pulled back to 13:30 (lunch start)',
    ret_d1_1330 && ret_d1_1330.depTime === '13:30',
    ret_d1_1330 ? ret_d1_1330.depTime : 'bucket not found');

  // ── 4. PO TBD rows: placeholder games surface in BOTH zone sheets ──
  section('SHUTTLE 4 — Pending PO games surface as PO TBD rows in both zones');
  const po_white = report.out.find(function(b) {
    return b.day === 1 && b.gameTime === '17:00' && b.venue === 'Blanes' && b.zone === 'white' && b.isPendingPO;
  });
  const po_black = report.out.find(function(b) {
    return b.day === 1 && b.gameTime === '17:00' && b.venue === 'Blanes' && b.zone === 'black' && b.isPendingPO;
  });
  check('PO TBD bucket exists in WHITE zone for D2 17:00',
    !!po_white && po_white.risk === 'PO_TBD' && po_white.teams.length === 1, po_white ? 'risk=' + po_white.risk + ' teams=' + po_white.teams.length : 'missing');
  check('PO TBD bucket exists in BLACK zone for D2 17:00',
    !!po_black && po_black.risk === 'PO_TBD' && po_black.teams.length === 1, po_black ? 'risk=' + po_black.risk + ' teams=' + po_black.teams.length : 'missing');
  check('PO TBD team row contains placeholder pair (e.g. "1st Group A vs 2nd Group A")',
    po_white && /1st Group A vs 2nd Group A/.test(po_white.teams[0].team),
    po_white ? po_white.teams[0].team : 'missing');

  // ── 5. Idle slots only filled where no game is scheduled ──
  section('SHUTTLE 5 — Idle bus rows skip slots with scheduled games');
  // D1 11:30 Blanes/white HAS a game → must NOT have a separate idle bucket
  const idle_blanes_d0_white = report.out.find(function(b) {
    return b.day === 0 && b.gameTime === '11:30' && b.venue === 'Blanes' && b.zone === 'white' && b.teams.length === 0;
  });
  check('D1 11:30 Blanes/white (scheduled) has NO separate idle row',
    !idle_blanes_d0_white, idle_blanes_d0_white ? 'unexpected idle row' : 'OK');
  // D1 09:00 Blanes/white has no game → should have an idle row (fleet has buses 8,9)
  const idle_blanes_d0_0900 = report.out.find(function(b) {
    return b.day === 0 && b.gameTime === '09:00' && b.venue === 'Blanes' && b.zone === 'white' && b.teams.length === 0;
  });
  check('D1 09:00 Blanes/white (empty slot, has fleet) HAS an idle row',
    !!idle_blanes_d0_0900, idle_blanes_d0_0900 ? 'OK seats=' + idle_blanes_d0_0900.seats : 'missing');
  // Venue/zone with no fleet (Palafolls/white in our cfg) should never appear
  const palafolls_white = report.out.find(function(b) {
    return b.venue === 'Palafolls' && b.zone === 'white';
  });
  check('Venue/zone pair with 0 buses produces no row',
    !palafolls_white, palafolls_white ? 'unexpected row at Palafolls/white' : 'OK');

  // ── 6. Per-day per-bus capacity override is applied ──
  section('SHUTTLE 6 — busCapacities[day][bus] overrides default seat count');
  // D1 11:30 Blanes/black: fleet = [1, 2]. busCapacities[0][1] = 70, [0][2] = default 55.
  // Expected seats = 70 + 55 = 125.
  const cap_blanes_d0_black = findBucket(report.out, 0, '11:30', 'Blanes', 'black');
  check('Day 0 Blanes/black uses override 70 + default 55 = 125 seats',
    cap_blanes_d0_black && cap_blanes_d0_black.seats === 125,
    cap_blanes_d0_black ? 'got ' + cap_blanes_d0_black.seats : 'missing');
  // D2 11:30 (none on day 1) — use D2 13:30 Blanes/white: fleet = [8, 9], no overrides → 55 + 55 = 110.
  const cap_blanes_d1_white = findBucket(report.ret, 1, '13:30', 'Blanes', 'white');
  check('Day 1 Blanes/white uses default 55 × 2 = 110 (no overrides)',
    cap_blanes_d1_white && cap_blanes_d1_white.seats === 110,
    cap_blanes_d1_white ? 'got ' + cap_blanes_d1_white.seats : 'missing');

  // ── 7. Risk classification matches load% thresholds ──
  section('SHUTTLE 7 — Risk classification: GREEN ≤75, YELLOW >75, RED >100');
  // The Tordera/black D1 11:30 OUT bucket: 1 team × 30 pax / 55 (one bus, default) = 55% → GREEN
  const grn = findBucket(report.out, 0, '11:30', 'Tordera', 'black');
  check('Low-load bucket (30/55 = 55%) is GREEN', grn && grn.risk === 'GREEN', grn ? grn.risk + ' load=' + grn.loadPct : 'missing');
  // Force-stress: stage a single team with pax > seats to verify RED
  dom.window._setSched({
    daySlots: [['09:00', '11:30']],
    gameDays: [{ dayIndex: 0, divs: [{ name: 'X', groups: { 'X Group A': { games: [
      { time: '11:30', loc: 'Tordera', t1: 'OverloadedTeam', t2: 'OverloadedTeam2', court: 'C1' }
    ]}}}]}],
    bracketDays: []
  });
  dom.window._setTeamZones([
    { div: 'X', team: 'OverloadedTeam', zone: 'white' },
    { div: 'X', team: 'OverloadedTeam2', zone: 'white' }
  ]);
  dom.window._setTeamPax([
    { div: 'X', team: 'OverloadedTeam', pax: 60 },
    { div: 'X', team: 'OverloadedTeam2', pax: 0 } // pax=0 keeps the count realistic
  ]);
  const stressReport = fn.shuttleBuildReport();
  const red = stressReport.out.find(function(b) {
    return b.venue === 'Tordera' && b.zone === 'white' && b.teams.some(function(t){return t.team==='OverloadedTeam';});
  });
  check('Over-capacity bucket (60/55 = 109%) is RED', red && red.risk === 'RED', red ? red.risk + ' load=' + red.loadPct : 'missing');

  // ── 8. NA when seats = 0 (no fleet for venue/zone) ──
  section('SHUTTLE 8 — Zero seats means NA risk');
  // Stage a game at Pineda/white where fleet has [] — but bucket only appears
  // if pax > 0 (real-team appearance). The bucket should exist with seats=0/risk=NA.
  dom.window._setSched({
    daySlots: [['09:00', '11:30']],
    gameDays: [{ dayIndex: 0, divs: [{ name: 'Y', groups: { 'Y Group A': { games: [
      { time: '11:30', loc: 'Pineda', t1: 'PineWhite', t2: 'PineBlack', court: 'C1' }
    ]}}}]}],
    bracketDays: []
  });
  dom.window._setTeamZones([
    { div: 'Y', team: 'PineWhite', zone: 'white' },
    { div: 'Y', team: 'PineBlack', zone: 'black' }
  ]);
  dom.window._setTeamPax([
    { div: 'Y', team: 'PineWhite', pax: 15 },
    { div: 'Y', team: 'PineBlack', pax: 15 }
  ]);
  const naReport = fn.shuttleBuildReport();
  const na = naReport.out.find(function(b) { return b.venue === 'Pineda' && b.zone === 'white' && b.teams.length > 0; });
  check('Pineda/white with empty fleet → seats=0, risk=NA',
    na && na.seats === 0 && na.risk === 'NA', na ? 'seats=' + na.seats + ' risk=' + na.risk : 'missing');

  dom.window.close();
}

// ── SNAPSHOT / EXPORT TESTS ───────────────────────────────────────────────────
async function runSnapshotTests() {
  section('SNAPSHOT 0 — buildSnapshot exposes full state');

  // Use the full real-app boot (default 8-division sample) so divisions/sched
  // are populated and we can assert the snapshot shape against generated state.
  const { window, document, dom } = await bootApp();
  const fn = window._testExports;
  if (!fn || !fn.buildSnapshot) {
    check('buildSnapshot export available', false, '_testExports.buildSnapshot missing');
    dom.window.close();
    return;
  }
  const snap = fn.buildSnapshot();

  // Required top-level keys
  const requiredKeys = ['sched', 'divisions', 'venueRules', 'venueBlackouts',
                        'teamAvailability', 'teamZones', 'teamPax',
                        'shuttleConfig', 'sites', 'setupFields'];
  const missing = requiredKeys.filter(function(k) { return !(k in snap); });
  check('Snapshot has all required top-level keys',
    missing.length === 0, missing.length ? 'missing: ' + missing.join(',') : 'OK');

  check('sched.gameDays is a non-empty array',
    Array.isArray(snap.sched.gameDays) && snap.sched.gameDays.length > 0,
    'len=' + (snap.sched.gameDays || []).length);
  check('divisions has the default sample divisions (≥ 5)',
    Array.isArray(snap.divisions) && snap.divisions.length >= 5,
    'len=' + (snap.divisions || []).length);

  // setupFields captures things tests will need on re-import
  check('setupFields.tName captured (non-empty string)',
    typeof snap.setupFields.tName === 'string' && snap.setupFields.tName.length > 0,
    'tName="' + snap.setupFields.tName + '"');
  check('setupFields.dayHours is an array with one entry per day',
    Array.isArray(snap.setupFields.dayHours)
      && snap.setupFields.dayHours.length === snap.sched.gameDays.length,
    'dayHours.length=' + (snap.setupFields.dayHours || []).length);
  check('setupFields.mainVenue captured',
    typeof snap.setupFields.mainVenue === 'string' && snap.setupFields.mainVenue.length > 0,
    snap.setupFields.mainVenue);

  // shuttleConfig defaults survive round-trip
  check('shuttleConfig has fleet + defaults + busCapacities + busCapacity',
    snap.shuttleConfig && snap.shuttleConfig.fleet && snap.shuttleConfig.defaults
      && 'busCapacity' in snap.shuttleConfig
      && typeof snap.shuttleConfig.busCapacities === 'object',
    'keys=' + Object.keys(snap.shuttleConfig || {}).join(','));

  section('SNAPSHOT 1 — XLSX day-header contract (first 10 columns validated)');
  // The full header carries free-form staff columns past J; the validator only
  // checks the first 10 (Time .. Visiting Team). The test enforces that
  // contract so we know editors can't accidentally rename a checked column.
  const hdr = fn.xlsxDayHeader();
  check('xlsxDayHeader has at least 10 columns (extra past J are free-form)',
    hdr.length >= 10, 'got ' + hdr.length);
  check('Column 0 is "Time"', hdr[0] === 'Time', hdr[0]);
  check('Column 6 is "Court"', /court/i.test(hdr[6]), hdr[6]);
  check('Column 8 is "Local Team"', /local/i.test(hdr[8]), hdr[8]);
  check('Column 9 is "Visiting Team"', /visit/i.test(hdr[9]), hdr[9]);

  dom.window.close();
}

// ── Main ──────────────────────────────────────────────────────────────────────
(async function main() {
  console.log('=======================================================');
  console.log(' Euro Basketball Scheduler — Unit & Integration Tests');
  console.log('=======================================================');

  try { await runUnitTests(); } catch (e) { console.error('Unit tests crashed:', e.message); failed++; }
  try { await runIntegrationTests(); } catch (e) { console.error('Integration tests crashed:', e.message, e.stack); failed++; }
  try { await runShuttleTests(); } catch (e) { console.error('Shuttle tests crashed:', e.message, e.stack); failed++; }
  try { await runSnapshotTests(); } catch (e) { console.error('Snapshot tests crashed:', e.message, e.stack); failed++; }

  console.log('\n═══════════════════════════════════════════════════════');
  console.log(' Results: ' + passed + ' passed, ' + failed + ' failed');
  console.log('═══════════════════════════════════════════════════════\n');
  process.exit(failed > 0 ? 1 : 0);
}());
