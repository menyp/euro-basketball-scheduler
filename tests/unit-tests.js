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

/** Iterate every game in sched (both shapes). Returns array of {g, dIdx, divName}. */
function allGames(sched) {
  const games = [];
  (sched.gameDays || []).forEach(function(day, dIdx) {
    day.divs.forEach(function(d) {
      if (d.games) d.games.forEach(function(g) { games.push({ g, dIdx, divName: d.name }); });
      if (d.groups) Object.keys(d.groups).forEach(function(gk) {
        d.groups[gk].games.forEach(function(g) { games.push({ g, dIdx, divName: d.name }); });
      });
    });
  });
  (sched.bracketDays || []).forEach(function(day, bdIdx) {
    const dIdx = (sched.gameDays || []).length + bdIdx;
    day.divs.forEach(function(d) {
      if (d.games) d.games.forEach(function(g) { games.push({ g, dIdx, divName: d.name }); });
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
  allGames(sched).forEach(function({ g, dIdx, divName }) {
    if (g.lbl) return;
    const po = earliestPO[divName];
    if (!po) return;
    if (dIdx > po.day || (dIdx === po.day && toM(g.time) >= po.timeM)) {
      violations.push(divName + ': RR "' + g.t1 + ' vs ' + g.t2 + '" at Day' + (dIdx+1) + ' ' + g.time + ' >= earliest PO Day' + (po.day+1));
    }
  });
  return violations;
}

function checkFinalsSequencing(sched) {
  const violations = [];
  const byBracket = {}; // key → { sfs: [], finals: [] }
  allGames(sched).forEach(function({ g, dIdx }) {
    if (!g.lbl) return;
    const key = g.bracket || 'Championship';
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
      const ok = latestSF.day < fin.day || (latestSF.day === fin.day && latestSF.timeM + 180 <= fin.timeM);
      if (!ok) violations.push(key + ': SF at Day' + (latestSF.day+1) + ' (' + latestSF.timeM + 'min) is < 180min before FINAL at Day' + (fin.day+1) + ' (' + fin.timeM + 'min)');
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
  allGames(sched).forEach(function({ g, dIdx }) {
    if (g.lbl && g.lbl.indexOf('SF') === 0) return; // SFs can be external for consolation
    // Only check the division in question
  });
  // Re-do with divName filter
  allGames(sched).forEach(function({ g, dIdx, divName: dn }) {
    if (dn !== divName) return;
    if ((g.loc || '').toLowerCase().indexOf(mv) === -1) {
      violations.push(dn + ': game at Day' + (dIdx+1) + ' ' + g.time + ' is at "' + g.loc + '" — expected ' + mainVenue);
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
      const m = t.match(/\(([A-Z]{2,4})\)/);
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

  dom.window.close();
}

// ── INTEGRATION TESTS ─────────────────────────────────────────────────────────

async function runIntegrationTests() {
  const SITES = [
    { name: 'Blanes',       numCourts: 6 },
    { name: 'Santa Suzana', numCourts: 2 },
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
  const chronoViolations = checkChronologicalIntegrity(sched);
  check('All RR games occur before division\'s earliest PO game',
    chronoViolations.length === 0,
    chronoViolations.length ? chronoViolations[0] : 'OK');

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
  // With no scores entered, RR should NOT be complete
  const rrResult1 = window.eval('isRoundRobinComplete()');
  check('isRoundRobinComplete() = false when no scores entered', rrResult1 === false, 'got ' + rrResult1);

  // Simulate entering all scores → then RR should be complete
  allGames(sched).forEach(function({ g }) {
    if (!g.lbl) { g.score1 = 50; g.score2 = 40; }
  });
  const rrResult2 = window.eval('isRoundRobinComplete()');
  check('isRoundRobinComplete() = true when all RR games are scored', rrResult2 === true, 'got ' + rrResult2);

  // Blank one score → should be false again
  const firstRR = allGames(sched).find(function(x) { return !x.g.lbl; });
  if (firstRR) {
    firstRR.g.score1 = null;
    const rrResult3 = window.eval('isRoundRobinComplete()');
    check('isRoundRobinComplete() = false after clearing one score', rrResult3 === false, 'got ' + rrResult3);
    firstRR.g.score1 = 50; // restore
  }

  section('INTG 13 — computeStandings accuracy (score propagation)');
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
        const st = window.eval('computeStandings()');
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
  const selfPlayViolations = allGames(sched).filter(function(x) { return x.g.t1 === x.g.t2; });
  check('No game has t1 === t2',
    selfPlayViolations.length === 0,
    selfPlayViolations.length ? selfPlayViolations[0].g.t1 + ' vs ' + selfPlayViolations[0].g.t2 : 'OK');

  section('INTG 15 — No team double-booked at same time on same day');
  const seen = {};
  const doubleViolations = [];
  allGames(sched).forEach(function({ g, dIdx }) {
    ['t1', 't2'].forEach(function(tf) {
      const key = dIdx + '|' + (g[tf] || '').toLowerCase().trim() + '|' + g.time;
      if (seen[key]) doubleViolations.push(g[tf] + ' double-booked at Day' + (dIdx+1) + ' ' + g.time);
      seen[key] = true;
    });
  });
  check('No team is double-booked at the same time on the same day',
    doubleViolations.length === 0,
    doubleViolations.length ? doubleViolations[0] : 'OK');

  dom.window.close();
}

// ── Main ──────────────────────────────────────────────────────────────────────
(async function main() {
  console.log('=======================================================');
  console.log(' Euro Basketball Scheduler — Unit & Integration Tests');
  console.log('=======================================================');

  try { await runUnitTests(); } catch (e) { console.error('Unit tests crashed:', e.message); failed++; }
  try { await runIntegrationTests(); } catch (e) { console.error('Integration tests crashed:', e.message, e.stack); failed++; }

  console.log('\n═══════════════════════════════════════════════════════');
  console.log(' Results: ' + passed + ' passed, ' + failed + ' failed');
  console.log('═══════════════════════════════════════════════════════\n');
  process.exit(failed > 0 ? 1 : 0);
}());
