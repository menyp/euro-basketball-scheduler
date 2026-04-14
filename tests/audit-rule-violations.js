'use strict';

/**
 * audit-rule-violations.js — independent verification of AI audit claims
 *
 * Reproduces the user's config (default teams + Blanes=5 + Santa Suzana=2 +
 * Palafolls=2 + Pineda=1 = 10 courts, default rules) and runs each rule
 * check with both the "loose" interpretation (what the scheduler enforces
 * today) and the "strict" interpretation (what the AI reviewers used).
 *
 * Goal: produce hard numbers so we can proceed to fix the confirmed bugs
 * without acting on AI misreads.
 *
 * Run:  node tests/audit-rule-violations.js
 */

const path = require('path');
const fs   = require('fs');
const { JSDOM } = require('jsdom');

const htmlPath = path.resolve(__dirname, '..', 'index.html');
const html = fs.readFileSync(htmlPath, 'utf8');

function toM(t) {
  var p = (t || '00:00').split(':');
  return parseInt(p[0], 10) * 60 + parseInt(p[1] || 0, 10);
}
function toT(m) {
  return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0');
}

// Dedup-safe walker that flattens every placed game across gameDays + bracketDays
function walkAllGames(sched) {
  const out = [];
  const seen = new Set();
  (sched.gameDays || []).forEach(function(dayObj, di) {
    (dayObj.divs || []).forEach(function(d) {
      const dn = d.name || '';
      if (d.groups) Object.keys(d.groups).forEach(function(gk) {
        d.groups[gk].games.forEach(function(g) {
          if (!seen.has(g)) { seen.add(g); out.push({ g, divName: dn, dayIdx: di }); }
        });
      });
      if (d.games) d.games.forEach(function(g) {
        if (!seen.has(g)) { seen.add(g); out.push({ g, divName: dn, dayIdx: di }); }
      });
    });
  });
  // bracketDays is a merged view — dedup via reference so we don't double-count
  (sched.bracketDays || []).forEach(function(day, bi) {
    const dIdx = (sched.gameDays || []).length + bi;
    (day.divs || []).forEach(function(d) {
      if (d.games) d.games.forEach(function(g) {
        if (!seen.has(g)) { seen.add(g); out.push({ g, divName: d.name, dayIdx: dIdx }); }
      });
    });
  });
  return out;
}

// ─────────────────────────────────────────────────────────────────
// Boot jsdom with index.html, configure sites, click Generate
// ─────────────────────────────────────────────────────────────────
const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true });
dom.virtualConsole.removeAllListeners('jsdomError');
const w = dom.window;
const d = w.document;

// 1. Reduce Blanes from 6 → 5
const firstSnum = d.querySelectorAll('#siteList input.snum')[0];
firstSnum.value = '5';
firstSnum.dispatchEvent(new w.Event('input', { bubbles: true }));

// 2. Add Pineda venue via the +Add button
try { d.getElementById('addSiteBtn').click(); } catch (e) {}

// 3. Rename the new site to 'Pineda' and set courts to 1
const snameInputs = d.querySelectorAll('#siteList input.sname');
const snumInputs = d.querySelectorAll('#siteList input.snum');
const lastIdx = snameInputs.length - 1;
snameInputs[lastIdx].value = 'Pineda';
snameInputs[lastIdx].dispatchEvent(new w.Event('input', { bubbles: true }));
snumInputs[lastIdx].value = '1';
snumInputs[lastIdx].dispatchEvent(new w.Event('input', { bubbles: true }));

// 4. Click Generate (ignore jsdom scrollTo errors)
try { d.getElementById('genBtn').click(); } catch (e) {}

const sched = w.sched || {};
const unscheduled = w.unscheduledGames || [];
const result = w._schedResult || {};

const allGames = walkAllGames(sched);

// ─────────────────────────────────────────────────────────────────
// Rule checks
// ─────────────────────────────────────────────────────────────────

// Rule 2 — No team double-booked at the exact same time
function checkRule2() {
  const slots = {}; // key: "div|team|day|time" → count
  const violations = [];
  allGames.forEach(function(x) {
    const g = x.g;
    if (!g.time) return;
    [g.t1, g.t2].forEach(function(team) {
      if (!team || !team.trim()) return;
      const k = x.divName + '|' + team.trim().toLowerCase() + '|' + x.dayIdx + '|' + g.time;
      slots[k] = (slots[k] || 0) + 1;
      if (slots[k] === 2) {
        violations.push({
          team: team.trim(), divName: x.divName,
          dayIdx: x.dayIdx, time: g.time,
        });
      }
    });
  });
  return violations;
}

// Rule 3 — RR chronology (loose + strict)
function checkRule3() {
  const earliestPO = {};
  allGames.forEach(function(x) {
    const g = x.g;
    if (!g.lbl) return;
    const tm = toM(g.time);
    const cur = earliestPO[x.divName];
    if (!cur || x.dayIdx < cur.day || (x.dayIdx === cur.day && tm < cur.timeM)) {
      earliestPO[x.divName] = { day: x.dayIdx, timeM: tm };
    }
  });
  const strictV = [];
  const looseV = [];
  allGames.forEach(function(x) {
    const g = x.g;
    if (g.lbl) return; // only RR
    const po = earliestPO[x.divName];
    if (!po) return;
    const tm = toM(g.time);
    const afterPoStrict = (x.dayIdx > po.day) || (x.dayIdx === po.day && tm >= po.timeM);
    const afterPoLoose  = (x.dayIdx > po.day) || (x.dayIdx === po.day && tm >  po.timeM);
    if (afterPoStrict) strictV.push({ divName: x.divName, game: g, dayIdx: x.dayIdx, po });
    if (afterPoLoose)  looseV.push({ divName: x.divName, game: g, dayIdx: x.dayIdx, po });
  });
  return { strict: strictV, loose: looseV, earliestPO };
}

// Per-team timeline (used by Rule 6 and Rule 7)
function buildTeamTimelines() {
  const timelines = {};
  allGames.forEach(function(x) {
    const g = x.g;
    if (!g.time) return;
    [g.t1, g.t2].forEach(function(team) {
      if (!team || !team.trim()) return;
      const k = x.divName + '|' + team.trim().toLowerCase();
      if (!timelines[k]) timelines[k] = [];
      timelines[k].push({
        dayIdx: x.dayIdx, tm: toM(g.time),
        time: g.time, court: g.court, loc: g.loc || '',
        team: team.trim(), divName: x.divName,
      });
    });
  });
  return timelines;
}

// Rule 6 — team rest ≥ 90 min between consecutive games (same day)
function checkRule6(timelines) {
  const violations = [];
  Object.keys(timelines).forEach(function(k) {
    const games = timelines[k].slice().sort(function(a, b) {
      if (a.dayIdx !== b.dayIdx) return a.dayIdx - b.dayIdx;
      return a.tm - b.tm;
    });
    for (let i = 1; i < games.length; i++) {
      if (games[i].dayIdx !== games[i-1].dayIdx) continue;
      const gap = games[i].tm - games[i-1].tm;
      if (gap < 90) {
        violations.push({ ...games[i-1], nextTm: games[i].tm, nextLoc: games[i].loc, gap });
      }
    }
  });
  return violations;
}

// Rule 7 — venue-change rest
//   Loose (scheduler):  180 min start-to-start = 2-slot gap = 90 min actual rest OK
//   Strict (AI):        270 min start-to-start = 3-slot gap = 180 min actual rest OK
function checkRule7(timelines) {
  const looseV  = [];
  const strictV = [];
  Object.keys(timelines).forEach(function(k) {
    const games = timelines[k].slice().sort(function(a, b) {
      if (a.dayIdx !== b.dayIdx) return a.dayIdx - b.dayIdx;
      return a.tm - b.tm;
    });
    for (let i = 1; i < games.length; i++) {
      if (games[i].dayIdx !== games[i-1].dayIdx) continue;
      if (games[i].loc === games[i-1].loc) continue; // same venue — no constraint
      const gap = games[i].tm - games[i-1].tm;
      const entry = {
        team: games[i].team, divName: games[i].divName,
        dayIdx: games[i].dayIdx,
        fromTime: games[i-1].time, fromLoc: games[i-1].loc,
        toTime: games[i].time, toLoc: games[i].loc,
        gap, rest: gap - 90,
      };
      if (gap < 180) looseV.push(entry);
      if (gap < 270) strictV.push(entry);
    }
  });
  return { loose: looseV, strict: strictV };
}

// ─────────────────────────────────────────────────────────────────
// Run checks and print report
// ─────────────────────────────────────────────────────────────────
const rule2 = checkRule2();
const rule3 = checkRule3();
const timelines = buildTeamTimelines();
const rule6 = checkRule6(timelines);
const rule7 = checkRule7(timelines);

// Count PO games separately (Claude parsed 179, so let's see what we have)
let rrCount = 0, poCount = 0;
allGames.forEach(function(x) { if (x.g.lbl) poCount++; else rrCount++; });

function printHeader() {
  console.log('');
  console.log('════════════════════════════════════════════════════════════');
  console.log('  Audit Rule Verification — jsdom harness');
  console.log('  Config: default teams + Blanes=5 + SS=2 + Pal=2 + Pineda=1');
  console.log('════════════════════════════════════════════════════════════');
  console.log('');
  console.log('  Placed games     : ' + allGames.length + ' (RR ' + rrCount + ' + PO ' + poCount + ')');
  console.log('  Failed games     : ' + unscheduled.length);
  console.log('  Scheduler totals : ' + (result.scheduled || 0) + ' / ' + (result.total || 0));
  console.log('');
}
printHeader();

// Rule 2
console.log('─── Rule 2: No team double-booked (same team, same time) ───');
if (rule2.length === 0) {
  console.log('  ✅ 0 violations');
} else {
  console.log('  ❌ ' + rule2.length + ' violations');
  rule2.slice(0, 10).forEach(function(v) {
    console.log('    * ' + v.team + ' [' + v.divName + '] Day ' + (v.dayIdx+1) + ' ' + v.time);
  });
}
console.log('');

// Rule 3
console.log('─── Rule 3: RR before PO chronology ───');
console.log('  Loose  (RR <= PO, current scheduler):  ' + rule3.loose.length + (rule3.loose.length === 0 ? ' ✅' : ' ❌'));
console.log('  Strict (RR <  PO, correct):             ' + rule3.strict.length + (rule3.strict.length === 0 ? ' ✅' : ' ❌'));
if (rule3.strict.length > 0) {
  const byDiv = {};
  rule3.strict.forEach(function(v) { byDiv[v.divName] = (byDiv[v.divName] || 0) + 1; });
  console.log('  Strict by division:');
  Object.keys(byDiv).sort().forEach(function(div) {
    console.log('    - ' + div + ': ' + byDiv[div] + ' RR games conflict');
  });
  console.log('  First 6 strict violations:');
  rule3.strict.slice(0, 6).forEach(function(v) {
    console.log('    * ' + v.divName + ': "' + (v.game.t1 || '?') + ' vs ' + (v.game.t2 || '?')
      + '" @ Day ' + (v.dayIdx+1) + ' ' + v.game.time + ' (' + v.game.court + ')');
    console.log('      earliest PO for ' + v.divName + ' is Day ' + (v.po.day+1) + ' ' + toT(v.po.timeM));
  });
}
console.log('');

// Rule 6
console.log('─── Rule 6: Team rest ≥ 90 min ───');
if (rule6.length === 0) {
  console.log('  ✅ 0 violations');
} else {
  console.log('  ❌ ' + rule6.length + ' violations');
  rule6.slice(0, 5).forEach(function(v) {
    console.log('    * ' + v.team + ' [' + v.divName + '] Day ' + (v.dayIdx+1)
      + ' ' + v.time + ' → ' + toT(v.nextTm) + ' (gap ' + v.gap + ' min)');
  });
}
console.log('');

// Rule 7
console.log('─── Rule 7: Venue-change rest ───');
console.log('  Loose  (scheduler — 180 min start-to-start = 90 min actual rest): ' + rule7.loose.length + (rule7.loose.length === 0 ? ' ✅' : ' ❌'));
console.log('  Strict (AI rule  — 270 min start-to-start = 180 min actual rest): ' + rule7.strict.length + (rule7.strict.length === 0 ? ' ✅' : ' ❌ '));
if (rule7.strict.length > 0) {
  const byDiv = {};
  rule7.strict.forEach(function(v) { byDiv[v.divName] = (byDiv[v.divName] || 0) + 1; });
  console.log('  Strict by division:');
  Object.keys(byDiv).sort().forEach(function(div) {
    console.log('    - ' + div + ': ' + byDiv[div]);
  });
  console.log('  All strict violations:');
  rule7.strict.forEach(function(v, i) {
    console.log('    ' + (i+1).toString().padStart(2, ' ') + '. ' + v.team + ' [' + v.divName + '] Day ' + (v.dayIdx+1)
      + ' ' + v.fromTime + ' ' + v.fromLoc + ' → ' + v.toTime + ' ' + v.toLoc
      + ' (gap ' + v.gap + ' min, rest ' + v.rest + ' min)');
  });
}
console.log('');

// Summary vs Claude
console.log('════════════════════════════════════════════════════════════');
console.log('  Match vs Claude\'s audit');
console.log('════════════════════════════════════════════════════════════');
const claudeRule3Divs = 2;   // Claude: "2 divisions"
const claudeRule7Count = 14; // Claude: "14 team-instances"
const ourRule3Divs = new Set(rule3.strict.map(function(v) { return v.divName; })).size;
const ourRule7Count = rule7.strict.length;
console.log('  Rule 3 (divisions with violations):');
console.log('    Claude: ' + claudeRule3Divs);
console.log('    Ours  : ' + ourRule3Divs + (ourRule3Divs === claudeRule3Divs ? ' ✅ match' : ' ❌ mismatch'));
console.log('  Rule 7 (team-instances under strict interpretation):');
console.log('    Claude: ' + claudeRule7Count);
console.log('    Ours  : ' + ourRule7Count + (ourRule7Count === claudeRule7Count ? ' ✅ match' : (Math.abs(ourRule7Count - claudeRule7Count) <= 2 ? ' ⚠️ close' : ' ❌ mismatch')));
console.log('');
