'use strict';
const path = require('path');
const fs = require('fs');
const JSDOM = require('jsdom').JSDOM;
const htmlPath = path.resolve(__dirname, '..', 'index.html');
const html = fs.readFileSync(htmlPath, 'utf8');
const dom = new JSDOM(html, {
  runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true,
  url: 'file://' + htmlPath.replace(/\\/g, '/'),
  beforeParse(win) {
    win.alert = function() {}; win.confirm = function() { return true; };
    win.prompt = function() { return ''; }; win.scrollTo = function() {};
    win.console = { log: function(){}, warn: function(){}, error: function(){} };
  }
});
(async function() {
  var window = dom.window;
  var document = window.document;
  await new Promise(function(r) {
    if (document.readyState !== 'loading') { setTimeout(r, 80); }
    else { document.addEventListener('DOMContentLoaded', function() { setTimeout(r, 80); }); }
  });
  window._setSites([{name:'Blanes',numCourts:5},{name:'Santa Susanna',numCourts:2},{name:'Palafolls',numCourts:2}]);
  document.getElementById('parseBtn').click();
  await new Promise(function(r) { setTimeout(r, 80); });
  document.getElementById('genBtn').click();
  await new Promise(function(r) { setTimeout(r, 300); });
  var r = window._schedResult;
  var s = window.sched;
  // Count RR games per day
  var dayCount = {};
  (s.gameDays||[]).forEach(function(dayObj, di) {
    var total = 0, qv = 0;
    dayObj.divs.forEach(function(d) {
      var games = d.games ? d.games : [];
      if (d.groups) Object.keys(d.groups).forEach(function(gk){ games = games.concat(d.groups[gk].games); });
      games.forEach(function(g) {
        total++;
        if (g.quotaViolation) {
          qv++;
          console.log('QV day' + di + ':', d.name, g.time, g.t1, 'vs', g.t2);
        }
      });
    });
    console.log('Day ' + di + ': total=' + total + ' QV=' + qv);
  });
  // Team day counts
  var teamDays = {};
  (s.gameDays||[]).forEach(function(dayObj, di) {
    dayObj.divs.forEach(function(d) {
      var games = d.games ? d.games : [];
      if (d.groups) Object.keys(d.groups).forEach(function(gk){ games = games.concat(d.groups[gk].games); });
      games.forEach(function(g) {
        var k1 = (d.name||'') + ':' + g.t1;
        var k2 = (d.name||'') + ':' + g.t2;
        if (!teamDays[k1]) teamDays[k1] = {};
        if (!teamDays[k2]) teamDays[k2] = {};
        teamDays[k1][di] = (teamDays[k1][di]||0)+1;
        teamDays[k2][di] = (teamDays[k2][di]||0)+1;
      });
    });
  });
  // Find teams exceeding maxGPD
  var maxGPD = parseInt(document.getElementById('maxGPD').value) || 2;
  var violations = [];
  Object.keys(teamDays).forEach(function(team) {
    Object.keys(teamDays[team]).forEach(function(day) {
      if (teamDays[team][day] > maxGPD) {
        violations.push({ team: team, day: day, count: teamDays[team][day] });
      }
    });
  });
  console.log('Teams exceeding maxGPD=' + maxGPD + ':');
  violations.forEach(function(v) {
    console.log('  ', v.team, 'day=' + v.day, 'count=' + v.count);
  });
  console.log('Total QV from result:', r.quotaViolations);
  dom.window.close();
}());
