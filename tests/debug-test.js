'use strict';
const path = require('path');
const fs = require('fs');
const JSDOM = require('jsdom').JSDOM;

const htmlPath = path.resolve(__dirname, '..', 'index.html');
const html = fs.readFileSync(htmlPath, 'utf8');

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  resources: 'usable',
  pretendToBeVisual: true,
  url: 'file://' + htmlPath.replace(/\\/g, '/'),
  beforeParse: function(win) {
    win.alert = function() {};
    win.confirm = function() { return true; };
    win.prompt = function() { return ''; };
    win.scrollTo = function() {};
    win.console = { log: function(){}, warn: function(){}, error: function(){} };
  }
});

(async function() {
  var window = dom.window;
  var document = window.document;
  await new Promise(function(resolve) {
    if (document.readyState !== 'loading') {
      setTimeout(resolve, 80);
    } else {
      document.addEventListener('DOMContentLoaded', function() { setTimeout(resolve, 80); });
    }
  });

  window._setSites([
    { name: 'Blanes', numCourts: 5 },
    { name: 'Santa Suzana', numCourts: 2 },
    { name: 'Palafolls', numCourts: 2 }
  ]);

  document.getElementById('parseBtn').click();
  await new Promise(function(r) { setTimeout(r, 80); });
  document.getElementById('genBtn').click();
  await new Promise(function(r) { setTimeout(r, 200); });

  var result = window._schedResult;
  var sched = window.sched;

  var dayN2PO = 0;
  (sched.gameDays || []).forEach(function(dayObj, di) {
    var total = 0;
    dayObj.divs.forEach(function(d) {
      if (d.games) {
        total += d.games.length;
        d.games.forEach(function(g) {
          if (g.lbl && g.lbl.indexOf('SF') === 0) {
            dayN2PO++;
            console.log('Day' + di + ' SF:', g.time, g.court, '-', (d.name || '?'), g.lbl);
          }
        });
      } else if (d.groups) {
        Object.keys(d.groups).forEach(function(gk) { total += d.groups[gk].games.length; });
      }
    });
    console.log('gameDays[' + di + '] games:', total);
  });
  console.log('Total SFs in gameDays:', dayN2PO);

  var dayN1 = 0;
  var dayN1SFs = 0;
  (sched.bracketDays || []).forEach(function(dayObj) {
    dayObj.divs.forEach(function(d) {
      if (d.games) {
        dayN1 += d.games.length;
        d.games.forEach(function(g) {
          if (g.lbl && g.lbl.indexOf('SF') === 0) dayN1SFs++;
        });
      }
    });
  });
  console.log('bracketDays total:', dayN1, 'SFs on Day N-1:', dayN1SFs);
  console.log('failed:', result.failed, 'quotaViolations:', result.quotaViolations);

  dom.window.close();
}());
