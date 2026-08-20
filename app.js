const API_BASE = localStorage.getItem('geoaiApiUrl') || 'https://city-1-6jst.onrender.com';
const modes = {
  route: { title: 'Emergency route', button: 'Run route analysis' },
  connectivity: { title: 'Connectivity', button: 'Measure connectivity' },
  bottlenecks: { title: 'Busy Intersections', button: 'Find busy intersections' },
  accessibility: { title: 'Service Reachability', button: 'Measure service reachability' }
};
let mode = 'route';

const $ = (selector) => document.querySelector(selector);

const whyModal = $('#whyModal');
$('#whyButton').addEventListener('click', () => whyModal.showModal());
$('#closeWhy').addEventListener('click', () => whyModal.close());
whyModal.addEventListener('click', (event) => {
  if (event.target === whyModal) whyModal.close();
});

async function checkApi() {
  try { const response = await fetch(`${API_BASE}/`); if (!response.ok) throw new Error(); $('#apiStatus').textContent = 'API online'; $('.status-dot').style.background = '#328b83'; }
  catch { $('#apiStatus').textContent = 'API unavailable'; $('.status-dot').style.background = '#eb765b'; }
}
checkApi();

document.querySelectorAll('.tool').forEach((button) => button.addEventListener('click', () => {
  mode = button.dataset.mode;
  document.querySelectorAll('.tool').forEach((item) => item.classList.remove('active'));
  button.classList.add('active');
  $('#modeTitle').textContent = modes[mode].title;
  $('#submitText').textContent = modes[mode].button;
  $('#routeFields').classList.toggle('hidden', mode !== 'route');
  $('#targetField').classList.toggle('hidden', mode !== 'accessibility');
  $('#origin').required = mode === 'route';
  $('#destination').required = mode === 'route';
  $('#target').required = mode === 'accessibility';
}));

function query(params) { return new URLSearchParams(Object.entries(params).filter(([, value]) => value)); }
function metric(label, value, wide = false) { return `<div class="metric${wide ? ' metric-wide' : ''}"><div class="metric-label">${label}</div><div class="metric-value">${value}</div></div>`; }
function escapeHtml(value) { return String(value).replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[character])); }
function routeSteps(streets) {
  const namedStreets = streets.filter((street) => street && street !== 'Unnamed road');
  return namedStreets.length ? `<ol class="route-steps">${namedStreets.map((street) => `<li>${escapeHtml(street)}</li>`).join('')}</ol>` : '<p>No named streets in the route data.</p>';
}

$('#analysisForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('#error').classList.add('hidden');
  $('#resultSection').classList.add('hidden');
  $('#submitText').textContent = 'Working...';
  const city = $('#city').value.trim();
  const params = { city };
  let endpoint = mode;
  if (mode === 'route') Object.assign(params, { origin: $('#origin').value.trim(), destination: $('#destination').value.trim() });
  if (mode === 'accessibility') Object.assign(params, { target: $('#target').value.trim() });
  if (mode === 'bottlenecks') params.top_n = 5;

  try {
    const response = await fetch(`${API_BASE}/${endpoint}?${query(params)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'The API returned an error.');
    $('#resultTitle').textContent = `${modes[mode].title} in ${city}`;
    const result = $('#result');
    if (mode === 'route') {
      result.innerHTML = metric('Distance', `${data.distance_m >= 1000 ? (data.distance_m / 1000).toFixed(2) + ' km' : data.distance_m + ' m'}`) + metric('Route points', data.geojson.geometry.coordinates.length) + metric('Road changes', data.streets.filter((street) => street !== 'Unnamed road').length) + `<div class="metric metric-wide"><div class="metric-label">Route sequence</div>${routeSteps(data.streets)}</div><div class="map-frame metric-wide"><iframe title="Route map" src="${API_BASE}/map?${query({ city, origin: params.origin, destination: params.destination })}"></iframe></div>`;
      const mapParams = query({ city, origin: params.origin, destination: params.destination });
      $('#mapLink').href = `${API_BASE}/map?${mapParams}`;
      $('#mapLink').classList.remove('hidden');
    } else if (mode === 'connectivity') {
      result.innerHTML = metric('Nodes', data.nodes.toLocaleString()) + metric('Edges', data.edges.toLocaleString()) + metric('Average degree', data.avg_node_degree) + metric('Network note', escapeHtml(data.note), true);
      $('#mapLink').classList.add('hidden');
    } else if (mode === 'bottlenecks') {
      result.innerHTML = `<p class="result-note metric-wide">Centrality compares how many other intersections can be reached through each node. A higher percentage indicates a more connected, potentially important junction. Coordinates identify the intersection.</p>` + data.top_bottlenecks.map((item, index) => metric(`#${index + 1} intersection`, `${(item.centrality_score * 100).toFixed(2)}% centrality<br><small>${Number(item.lat).toFixed(5)}, ${Number(item.lon).toFixed(5)}</small>`)).join('');
      $('#mapLink').classList.add('hidden');
    } else {
      result.innerHTML = metric('Reachable intersections', data.reachable_nodes.toLocaleString()) + metric('Average network distance', `${data.avg_distance_m.toLocaleString()} m`) + metric('Target place', escapeHtml(params.target)) + `<div class="metric metric-wide"><div class="metric-label">Closest sampled intersections</div><p class="metric-help">${escapeHtml(data.distance_note)}</p><ol class="distance-list">${data.sample_distances_m.map((item) => `<li>${item.distance_m} m</li>`).join('')}</ol></div>`;
      $('#mapLink').classList.add('hidden');
    }
    $('#resultSection').classList.remove('hidden');
    $('#resultSection').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    $('#error').textContent = error.message;
    $('#error').classList.remove('hidden');
  } finally { $('#submitText').textContent = modes[mode].button; }
});
