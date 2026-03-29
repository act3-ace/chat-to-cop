"""HTML dashboard for operator visibility.

Single-page dashboard showing recent CoPUpdates and tracked entities.
Served by the FastAPI app at GET /dashboard (and GET /).
No build step, no npm — inline CSS/JS in a Python string.
Auto-refreshes every 5 seconds via JavaScript polling.
"""

from __future__ import annotations

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>chat-to-cop Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
    background: #0d1117; color: #c9d1d9; padding: 16px;
  }
  h1 { color: #58a6ff; margin-bottom: 4px; font-size: 1.4em; }
  .subtitle { color: #8b949e; font-size: 0.85em; margin-bottom: 16px; }
  .stats-bar {
    display: flex; gap: 24px; margin-bottom: 16px;
    padding: 8px 12px; background: #161b22; border-radius: 6px;
    border: 1px solid #30363d; font-size: 0.85em;
  }
  .stats-bar .stat-label { color: #8b949e; }
  .stats-bar .stat-value { color: #58a6ff; font-weight: bold; margin-left: 4px; }
  h2 { color: #c9d1d9; font-size: 1.1em; margin: 16px 0 8px 0; }
  .table-wrap {
    overflow-x: auto; border: 1px solid #30363d; border-radius: 6px;
    margin-bottom: 20px;
  }
  table { width: 100%; border-collapse: collapse; font-size: 0.82em; }
  th {
    background: #161b22; color: #8b949e; text-align: left;
    padding: 8px 10px; border-bottom: 1px solid #30363d;
    position: sticky; top: 0; font-weight: 600; white-space: nowrap;
  }
  td { padding: 6px 10px; border-bottom: 1px solid #21262d; }
  tr:hover { background: #161b22; }
  .conf-high { color: #3fb950; font-weight: bold; }
  .conf-mid  { color: #d29922; font-weight: bold; }
  .conf-low  { color: #f85149; font-weight: bold; }
  .method-tag {
    display: inline-block; padding: 1px 6px; border-radius: 3px;
    font-size: 0.8em; font-weight: 600;
  }
  .method-llm { background: #1f3a1f; color: #3fb950; }
  .method-regex { background: #3a2f1f; color: #d29922; }
  .method-passthrough { background: #3a1f1f; color: #f85149; }
  .msg-snippet { color: #8b949e; max-width: 300px; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; }
  .entity-summary { color: #c9d1d9; font-size: 0.9em; }
  .status-ok { color: #3fb950; }
  .status-degraded { color: #d29922; }
  .status-inop { color: #f85149; }
  .empty-msg { color: #8b949e; padding: 20px; text-align: center; }
  .refresh-indicator {
    position: fixed; top: 8px; right: 16px; font-size: 0.75em;
    color: #8b949e;
  }
  .refresh-indicator.active { color: #58a6ff; }
  .affil-friend { color: #58a6ff; }
  .affil-hostile { color: #f85149; }
  .affil-neutral { color: #d29922; }
  .affil-unknown { color: #8b949e; }
</style>
</head>
<body>

<h1>chat-to-cop Dashboard</h1>
<p class="subtitle">AI staff officer &mdash; world-state extraction from military chat</p>

<div class="stats-bar">
  <div><span class="stat-label">Updates:</span><span class="stat-value" id="stat-updates">-</span></div>
  <div><span class="stat-label">Entities:</span><span class="stat-value" id="stat-entities">-</span></div>
  <div><span class="stat-label">Status:</span><span class="stat-value" id="stat-status">loading</span></div>
</div>

<h2>Recent CoPUpdates</h2>
<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>Timestamp</th>
        <th>Channel</th>
        <th>Speaker</th>
        <th>Type</th>
        <th>Confidence</th>
        <th>Method</th>
        <th>Entities</th>
        <th>Message</th>
      </tr>
    </thead>
    <tbody id="updates-body">
      <tr><td colspan="8" class="empty-msg">Loading...</td></tr>
    </tbody>
  </table>
</div>

<h2>Tracked Entities</h2>
<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>Callsign / Track</th>
        <th>Platform Type</th>
        <th>Affiliation</th>
        <th>Status</th>
        <th>Position</th>
        <th>Last Updated</th>
      </tr>
    </thead>
    <tbody id="entities-body">
      <tr><td colspan="6" class="empty-msg">Loading...</td></tr>
    </tbody>
  </table>
</div>

<div class="refresh-indicator" id="refresh-ind">auto-refresh: 5s</div>

<script>
function escapeHtml(text) {
  if (!text) return '';
  var d = document.createElement('div');
  d.textContent = text;
  return d.innerHTML;
}

function confClass(c) {
  if (c > 0.8) return 'conf-high';
  if (c >= 0.5) return 'conf-mid';
  return 'conf-low';
}

function methodClass(m) {
  if (m === 'llm') return 'method-llm';
  if (m === 'regex') return 'method-regex';
  return 'method-passthrough';
}

function statusClass(s) {
  if (!s) return '';
  var sl = s.toLowerCase();
  if (sl === 'operational') return 'status-ok';
  if (sl === 'degraded' || sl === 'rtb') return 'status-degraded';
  return 'status-inop';
}

function affilClass(a) {
  if (!a) return 'affil-unknown';
  var al = a.toLowerCase();
  if (al === 'friend' || al === 'friendly') return 'affil-friend';
  if (al === 'hostile') return 'affil-hostile';
  if (al === 'neutral') return 'affil-neutral';
  return 'affil-unknown';
}

function formatTimestamp(ts) {
  if (!ts) return '-';
  var d = new Date(ts);
  return d.toISOString().replace('T', ' ').substring(0, 19) + 'Z';
}

function summarizeEntities(entities) {
  if (!entities || entities.length === 0) return '-';
  return entities.map(function(e) {
    var parts = [];
    if (e.callsign) parts.push(e.callsign);
    if (e.track_number) parts.push(e.track_number);
    if (e.platform_type) parts.push(e.platform_type);
    if (e.affiliation) parts.push(e.affiliation);
    if (e.operational_status) parts.push(e.operational_status);
    if (e.weapon_type) parts.push(e.weapon_type);
    if (e.fuel_state) parts.push(e.fuel_state);
    return parts.join(' / ');
  }).join('; ');
}

function formatPosition(lat, lon) {
  if (lat == null || lon == null) return '-';
  return lat.toFixed(3) + ', ' + lon.toFixed(3);
}

function renderUpdates(updates) {
  var tbody = document.getElementById('updates-body');
  if (!updates || updates.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-msg">No updates yet</td></tr>';
    return;
  }
  var html = '';
  for (var i = 0; i < updates.length; i++) {
    var u = updates[i];
    html += '<tr>';
    html += '<td>' + formatTimestamp(u.timestamp) + '</td>';
    html += '<td>' + escapeHtml(u.source_channel) + '</td>';
    html += '<td>' + escapeHtml(u.source_speaker) + '</td>';
    html += '<td>' + escapeHtml(u.update_type) + '</td>';
    html += '<td class="' + confClass(u.confidence) + '">'
          + u.confidence.toFixed(2) + '</td>';
    html += '<td><span class="method-tag ' + methodClass(u.extraction_method)
          + '">' + escapeHtml(u.extraction_method) + '</span></td>';
    html += '<td class="entity-summary">'
          + escapeHtml(summarizeEntities(u.entities)) + '</td>';
    html += '<td class="msg-snippet" title="'
          + escapeHtml(u.source_message) + '">'
          + escapeHtml(u.source_message) + '</td>';
    html += '</tr>';
  }
  tbody.innerHTML = html;
}

function renderEntities(entities) {
  var tbody = document.getElementById('entities-body');
  if (!entities || entities.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-msg">No entities tracked</td></tr>';
    return;
  }
  var html = '';
  for (var i = 0; i < entities.length; i++) {
    var e = entities[i];
    var name = e.callsign || e.track_number || e.entity_key || '-';
    html += '<tr>';
    html += '<td>' + escapeHtml(name) + '</td>';
    html += '<td>' + escapeHtml(e.platform_type || '-') + '</td>';
    html += '<td class="' + affilClass(e.affiliation) + '">'
          + escapeHtml(e.affiliation || 'UNKNOWN') + '</td>';
    html += '<td class="' + statusClass(e.operational_status) + '">'
          + escapeHtml(e.operational_status || '-') + '</td>';
    html += '<td>' + formatPosition(e.last_known_lat, e.last_known_lon) + '</td>';
    html += '<td>' + formatTimestamp(e.last_updated) + '</td>';
    html += '</tr>';
  }
  tbody.innerHTML = html;
}

function refresh() {
  var ind = document.getElementById('refresh-ind');
  ind.className = 'refresh-indicator active';

  Promise.all([
    fetch('/updates?limit=100').then(function(r) { return r.json(); }),
    fetch('/entities').then(function(r) { return r.json(); }),
    fetch('/health').then(function(r) { return r.json(); })
  ]).then(function(results) {
    renderUpdates(results[0]);
    renderEntities(results[1]);
    document.getElementById('stat-updates').textContent = results[2].updates_count;
    document.getElementById('stat-entities').textContent = results[2].entities_count;
    document.getElementById('stat-status').textContent = results[2].status;
    ind.className = 'refresh-indicator';
  }).catch(function(err) {
    console.error('Refresh error:', err);
    document.getElementById('stat-status').textContent = 'error';
    ind.className = 'refresh-indicator';
  });
}

refresh();
setInterval(refresh, 5000);
</script>

</body>
</html>"""
