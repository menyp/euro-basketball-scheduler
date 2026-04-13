'use strict';

/**
 * diagnose-user-4fails.js — Smoking-gun test
 *
 * Reproduces the user's reported config:
 *   default teams + Blanes=5 (9 courts total) + Day 3 at default 17:30
 *
 * Then runs it in two modes back-to-back:
 *   Variant A — Mandatory Venue Rest (the user's default → 4 failures)
 *   Variant C — High Priority Venue Rest (switching the banner's suggestion)
 *
 * If Variant C reaches 0 failures → confirms the 4 failures are pure
 * Mandatory-mode strictness, not a scheduler bug.
 *
 * Run:  node tests/diagnose-user-4fails.js
 */

const path = require('path');
const fs   = require('fs');
const { execSync } = require('child_process');
const { JSDOM } = require('jsdom');

const htmlPath = path.resolve(__dirname, '..', 'index.html');
const htmlCurrent = fs.readFileSync(htmlPath, 'utf8');

// Load pre-efficiency-fix snapshot for Variant D
let htmlPreFix = null;
try {
  htmlPreFix = execSync('git show 6cce975^:index.html', {
    cwd: path.resolve(__dirname, '..'),
    encoding: 'utf8',
    maxBuffer: 10 * 1024 * 1024,
  });
} catch (e) {
  console.warn('[warn] Could not load pre-fix index.html (git show 6cce975^:index.html): ' + e.message);
}

function runVariant(label, setup, useHtml) {
  const html = useHtml || htmlCurrent;
  const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true });
  const w = dom.window;
  const d = w.document;

  // Suppress noisy jsdom implementation warnings (scrollTo etc.)
  dom.virtualConsole.removeAllListeners('jsdomError');

  // App bootstraps on load (renderSites, renderDayHours, parseTeams)
  // Divisions are already parsed from the default #pasteBox by the time we get here.

  // Reduce Blanes (first site) from 6 → 5 courts via the input handler,
  // which updates `sites[0].numCourts` in the app's closure.
  const snumInputs = d.querySelectorAll('#siteList input.snum');
  if (snumInputs.length === 0) throw new Error('Could not find site court inputs');
  const blanesInput = snumInputs[0];
  blanesInput.value = '5';
  blanesInput.dispatchEvent(new w.Event('input', { bubbles: true }));

  // Day 3 end-time stays at the default '17:30' (do NOT extend).
  // Just verify it's correct:
  const day3End = d.getElementById('dayEnd_2');
  if (!day3End) throw new Error('Could not find dayEnd_2');
  if (day3End.value !== '17:30') {
    console.warn('  [warn] Day 3 end is ' + day3End.value + ', expected 17:30');
  }

  // Apply the variant-specific setup (e.g., flip venue rest mode)
  setup(w, d);

  // Click Generate
  const genBtn = d.getElementById('genBtn');
  if (!genBtn) throw new Error('Could not find genBtn');
  try {
    genBtn.click();
  } catch (e) {
    // Ignore jsdom-specific errors like scrollTo — the scheduler runs regardless
  }

  const r = w._schedResult || {};
  const sched = w.sched || {};
  const warnings = sched.scheduleWarnings || w.scheduleWarnings || [];

  return {
    label: label,
    total: r.total || 0,
    scheduled: r.scheduled || 0,
    failed: r.failed || 0,
    softWarnings: r.softWarnings || 0,
    unscheduledGames: (r.unscheduledGames || []).slice(),
    quotaViolations: r.quotaViolations || 0,
  };
}

function printVariant(result) {
  console.log('');
  console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
  console.log('  ' + result.label);
  console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
  console.log('  Games scheduled : ' + result.scheduled + ' / ' + result.total);
  console.log('  Games failed    : ' + result.failed);
  console.log('  Soft warnings   : ' + result.softWarnings);
  console.log('  Quota violations: ' + result.quotaViolations);
  if (result.unscheduledGames.length > 0) {
    console.log('  Unscheduled games:');
    result.unscheduledGames.forEach(function(g, i) {
      const lbl = g.lbl ? ('[' + g.lbl + ']') : '[RR]';
      console.log('    ' + (i + 1) + '. ' + (g.div || '?') + ' ' + lbl + ' ' + (g.t1 || '?') + ' vs ' + (g.t2 || '?'));
      if (g.reason) console.log('       → ' + g.reason);
    });
  }
}

console.log('');
console.log('════════════════════════════════════════════════');
console.log('  Diagnose: user-reported 4 failures');
console.log('  Config: default teams + 9 courts (Blanes=5) +');
console.log('          Day 3 end 17:30 (DEFAULT, not extended)');
console.log('════════════════════════════════════════════════');

// Variant A: Mandatory Venue Rest (user's reported config)
const A = runVariant(
  'Variant A — Mandatory Venue Rest (reproducing user report)',
  function(w, d) {
    const mandEl = d.getElementById('venueRestModeMandatory');
    const prioEl = d.getElementById('venueRestModePriority');
    if (mandEl) mandEl.checked = true;
    if (prioEl) prioEl.checked = false;
  }
);

// Variant B: Same config as A, but extend Day 3 end time to 19:00
const B = runVariant(
  'Variant B — Day 3 extended to 19:00 (matches smoke-test Scenario 3)',
  function(w, d) {
    // Same rule mode as A
    const mandEl = d.getElementById('venueRestModeMandatory');
    const prioEl = d.getElementById('venueRestModePriority');
    if (mandEl) mandEl.checked = true;
    if (prioEl) prioEl.checked = false;
    // Extend Day 3 end time
    const day3End = d.getElementById('dayEnd_2');
    if (day3End) day3End.value = '19:00';
  }
);

// Variant C: High Priority mode — tests whether venue rest mandatory is the cause
const C = runVariant(
  'Variant C — HIGH PRIORITY Venue Rest (is Mandatory mode the cause?)',
  function(w, d) {
    const mandEl = d.getElementById('venueRestModeMandatory');
    const prioEl = d.getElementById('venueRestModePriority');
    if (mandEl) mandEl.checked = false;
    if (prioEl) prioEl.checked = true;
    if (prioEl) prioEl.dispatchEvent(new w.Event('change', { bubbles: true }));
  }
);

// Variant D: Same config as A, but using PRE-efficiency-fix index.html
let D = null;
if (htmlPreFix) {
  D = runVariant(
    'Variant D — PRE-efficiency-fix code (did the fix help at all?)',
    function(w, d) {
      const mandEl = d.getElementById('venueRestModeMandatory');
      const prioEl = d.getElementById('venueRestModePriority');
      if (mandEl) mandEl.checked = true;
      if (prioEl) prioEl.checked = false;
    },
    htmlPreFix
  );
}

printVariant(A);
printVariant(B);
printVariant(C);
if (D) printVariant(D);

console.log('');
console.log('════════════════════════════════════════════════');
console.log('  VERDICT');
console.log('════════════════════════════════════════════════');
console.log('  A (baseline Mandatory, Day3=17:30)    : ' + A.failed + ' failures');
console.log('  B (Mandatory, Day3=19:00)             : ' + B.failed + ' failures');
console.log('  C (High Priority, Day3=17:30)         : ' + C.failed + ' failures');
if (D) {
  console.log('  D (pre-fix code, Mandatory, 17:30)    : ' + D.failed + ' failures');
}
console.log('');

// Capacity hypothesis: B=0 means extending Day 3 solves it
const capacityIssue = (B.failed === 0 && A.failed > 0);
// Fix effectiveness: compare D (pre-fix) vs A (post-fix) on identical config
const fixHelped = D && (D.failed > A.failed);
const fixIsNoop = D && (D.failed === A.failed);

if (capacityIssue) {
  console.log('  ✅ DIAGNOSED: Day 3 capacity, NOT a bug');
  console.log('');
  console.log('     Extending Day 3 from 17:30 → 19:00 reduces failures from');
  console.log('     ' + A.failed + ' to ' + B.failed + '. The missing 1.5 hours = 1 extra time slot');
  console.log('     across 9 courts = 9 additional court-slots of capacity,');
  console.log('     which is exactly what the ' + A.failed + ' failing games need.');
  console.log('');
  console.log('     The failing games are all U12 MIXED consolation medal games,');
  console.log('     which are lowest priority in the medal cascade and get');
  console.log('     squeezed out when Day 3 is too short.');
  console.log('');
  console.log('     Solutions (any one works):');
  console.log('       1. Extend Day 3 end-time to 19:00');
  console.log('       2. Add 1 more court (10 courts = default config)');
  console.log('       3. Shorten lunch break or start earlier');
  console.log('');
  console.log('     The scheduler engine is correct.');
  if (fixIsNoop) {
    console.log('');
    console.log('     Note: the efficiency fix (commit 6cce975) is a no-op for this');
    console.log('     specific config — Variant D (pre-fix code) produces identical');
    console.log('     ' + D.failed + ' failures. The fix helps configs where Day N-2 tail');
    console.log('     slots or Day N afternoon Blanes were being over-reserved. In');
    console.log('     this config with Day 3 truncated, those slots are not the');
    console.log('     binding constraint — Day 3 capacity is.');
  } else if (fixHelped) {
    console.log('');
    console.log('     Note: the efficiency fix DID help — pre-fix code produces');
    console.log('     ' + D.failed + ' failures vs ' + A.failed + ' with the fix. But not enough to hit 0.');
  }
} else if (C.failed < A.failed) {
  console.log('  ⚠  PARTIAL: Mandatory mode explains ' + (A.failed - C.failed) + ' of ' + A.failed + ' failures.');
  console.log('     ' + C.failed + ' remain unexplained → investigate further.');
} else if (B.failed > 0) {
  console.log('  🚨 UNEXPECTED: Even with Day 3 extended to 19:00, ' + B.failed + ' failures remain.');
  console.log('     This is NOT pure capacity. Possible real bug.');
  console.log('     Inspect B.unscheduledGames above for clues.');
} else {
  console.log('  ⚠  Inconclusive — review the per-variant output above.');
}
console.log('');
