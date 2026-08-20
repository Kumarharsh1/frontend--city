import html
import json
import math
import re
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import osmnx as ox
import networkx as nx
import requests

app = FastAPI(title="GeoAI Smart City Platform")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/")
def home() -> dict[str, object]:
    return {
        "status": "ok",
        "message": "GeoAI Smart City Platform is running",
        "docs": "/docs",
        "map_example": "/map?city=any%20region&origin=origin%20place&destination=destination%20place",
    }


# ---------------------------------------------------------
# Simple in-memory cache so we don't re-download the same
# city's road network on every request.
# ---------------------------------------------------------
_graph_cache: dict[str, nx.MultiDiGraph] = {}
_geocode_cache: dict[tuple[str, str], tuple[float, float]] = {}
_route_cache: dict[tuple[str, str, str], dict[str, object]] = {}
_analysis_cache: dict[tuple[object, ...], dict[str, object]] = {}
_graph_load_lock = Lock()
_graph_cache_dir = Path(__file__).parent / "cache" / "graphs"


def _city_key(city: str) -> str:
    return " ".join(city.strip().lower().split())


def _clean_street_name(value: object) -> str | None:
    parts = value if isinstance(value, (list, tuple)) else [value]
    names = []
    for part in parts:
        if part is None or (isinstance(part, float) and math.isnan(part)):
            continue
        name = str(part).strip()
        if name and name.lower() != "nan" and name not in names:
            names.append(name)
    return " / ".join(names) or None

def get_graph(city: str) -> nx.MultiDiGraph:
    city_key = _city_key(city)
    if city_key in _graph_cache:
        return _graph_cache[city_key]

    # The lock prevents two first requests for the same city from both downloading it.
    with _graph_load_lock:
        if city_key in _graph_cache:
            return _graph_cache[city_key]

        _graph_cache_dir.mkdir(parents=True, exist_ok=True)
        city_slug = re.sub(r"[^a-z0-9]+", "_", city_key).strip("_")[:120]
        graph_path = _graph_cache_dir / f"{city_slug or 'city'}.graphml"
        if graph_path.exists():
            graph = ox.load_graphml(graph_path)
        else:
            print(f"Downloading graph for {city} (not cached yet)...")
            graph = ox.graph_from_place(city, network_type="drive")
            ox.save_graphml(graph, graph_path)
        _graph_cache[city_key] = graph
        return graph


def geocode_place(place: str, city: str) -> tuple[float, float]:
    """
    Turn a place name (e.g. 'Ranchi Railway Station') into (lat, lon).
    Appends the city if not already mentioned, to improve match accuracy.
    """
    place_name = place.strip()
    cache_key = (city.lower().strip(), place_name.lower())
    if cache_key in _geocode_cache:
        return _geocode_cache[cache_key]

    city_name = city.split(",")[0].strip()
    queries = [
        f"{place_name}, {city}" if city_name.lower() not in place_name.lower() else place_name,
        place_name,
    ]

    for query in dict.fromkeys(queries):
        try:
            lat, lon = ox.geocode(query)
            _geocode_cache[cache_key] = (lat, lon)
            return _geocode_cache[cache_key]
        except Exception:
            continue

    raise HTTPException(
        status_code=404,
        detail=f"Could not find a location for '{place}'. Try a more specific or well-known place name (e.g. a landmark, hospital, or station name as it appears on Google Maps)."
    )


def _ensure_location_in_graph(
    graph: nx.MultiDiGraph,
    location: str,
    latitude: float,
    longitude: float,
    city: str,
) -> None:
    """Prevent a place outside the selected city's graph being snapped locally."""
    longitudes = [data["x"] for _, data in graph.nodes(data=True)]
    latitudes = [data["y"] for _, data in graph.nodes(data=True)]
    if not longitudes or not latitudes:
        raise HTTPException(status_code=422, detail=f"The road graph for '{city}' is empty.")

    # A small margin allows geocoded landmarks just beyond a place boundary.
    margin = 0.05
    in_graph = (
        min(longitudes) - margin <= longitude <= max(longitudes) + margin
        and min(latitudes) - margin <= latitude <= max(latitudes) + margin
    )
    if not in_graph:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{location}' is outside the selected road network for '{city}'. "
                f"Choose a city or region containing both locations. "
                f"For example, use 'Mumbai, India' when measuring access to a Mumbai place."
            ),
        )


# ---------------------------------------------------------
# 1. ROUTE (Emergency Route Optimizer) — now returns GeoJSON
#    Accepts place NAMES, not raw coordinates.
# ---------------------------------------------------------
@app.get("/route")
def get_route(city: str, origin: str, destination: str):
    route_key = (_city_key(city), origin.strip().lower(), destination.strip().lower())
    if route_key in _route_cache:
        return _route_cache[route_key]

    orig_lat, orig_lon = geocode_place(origin, city)
    dest_lat, dest_lon = geocode_place(destination, city)
    try:
        response = requests.get(
            "https://router.project-osrm.org/route/v1/driving/"
            f"{orig_lon},{orig_lat};{dest_lon},{dest_lat}",
            params={"overview": "full", "geometries": "geojson", "steps": "true"},
            timeout=30,
        )
        response.raise_for_status()
        routing_data = response.json()
    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail="The global routing service is temporarily unavailable. Please try again.",
        ) from error

    if routing_data.get("code") != "Ok" or not routing_data.get("routes"):
        raise HTTPException(
            status_code=422,
            detail=f"No drivable route was found between '{origin}' and '{destination}'.",
        )

    selected_route = routing_data["routes"][0]
    route_coordinates = selected_route["geometry"]["coordinates"]
    streets = []
    for leg in selected_route.get("legs", []):
        for step in leg.get("steps", []):
            name = _clean_street_name(step.get("name")) or "Unnamed road"
            if not streets or streets[-1] != name:
                streets.append(name)
    if not streets:
        streets = ["Unnamed road"]

    geojson = {
        "type": "Feature",
        "properties": {"city": city, "origin": origin, "destination": destination, "streets": streets},
        "geometry": {
            "type": "LineString",
            "coordinates": route_coordinates,
        }
    }

    route_data = {
        "city": city,
        "origin": {"name": origin, "lat": orig_lat, "lon": orig_lon},
        "destination": {"name": destination, "lat": dest_lat, "lon": dest_lon},
        "route_nodes": list(range(len(route_coordinates))),
        "streets": streets,
        "distance_m": round(selected_route["distance"], 1),
        "geojson": geojson
    }
    _route_cache[route_key] = route_data
    return route_data


# ---------------------------------------------------------
# 2. CONNECTIVITY (Village Connectivity / network density)
# ---------------------------------------------------------
@app.get("/connectivity")
def get_connectivity(city: str):
    cache_key = ("connectivity", _city_key(city))
    if cache_key in _analysis_cache:
        return _analysis_cache[cache_key]

    G = get_graph(city)
    n_nodes = len(G.nodes)
    n_edges = len(G.edges)
    avg_degree = round(2 * n_edges / n_nodes, 2) if n_nodes else 0

    result = {
        "city": city,
        "nodes": n_nodes,
        "edges": n_edges,
        "avg_node_degree": avg_degree,
        "note": "Lower avg_node_degree generally indicates sparser, less-connected road network."
    }
    _analysis_cache[cache_key] = result
    return result


# ---------------------------------------------------------
# 3. BOTTLENECKS (Traffic Bottleneck Analysis)
# ---------------------------------------------------------
@app.get("/bottlenecks")
def get_bottlenecks(city: str, top_n: int = 5):
    cache_key = ("bottlenecks", _city_key(city), top_n)
    if cache_key in _analysis_cache:
        return _analysis_cache[cache_key]

    G = get_graph(city)
    degree_centrality = nx.degree_centrality(G)
    sorted_nodes = sorted(degree_centrality.items(), key=lambda x: x[1], reverse=True)[:top_n]

    results = []
    for node_id, score in sorted_nodes:
        node = G.nodes[node_id]
        results.append({
            "node_id": node_id,
            "centrality_score": round(score, 4),
            "lat": node["y"],
            "lon": node["x"],
            "meaning": "Higher centrality means more of the road network passes through or connects at this intersection."
        })

    result = {"city": city, "top_bottlenecks": results}
    _analysis_cache[cache_key] = result
    return result


# ---------------------------------------------------------
# 4. ACCESSIBILITY (distance from all nodes to a target, e.g. hospital)
# ---------------------------------------------------------
@app.get("/accessibility")
def get_accessibility(city: str, target: str, sample: int = 10):
    cache_key = ("accessibility", _city_key(city), target.strip().lower(), sample)
    if cache_key in _analysis_cache:
        return _analysis_cache[cache_key]

    G = get_graph(city)
    target_lat, target_lon = geocode_place(target, city)
    _ensure_location_in_graph(G, target, target_lat, target_lon, city)
    target_node = ox.nearest_nodes(G, target_lon, target_lat)
    lengths = nx.shortest_path_length(G, target=target_node, weight="length")

    sample_items = sorted(lengths.items(), key=lambda item: item[1])[:sample]
    avg_distance = round(sum(lengths.values()) / len(lengths), 1) if lengths else 0

    result = {
        "city": city,
        "target": {"lat": target_lat, "lon": target_lon},
        "reachable_nodes": len(lengths),
        "avg_distance_m": avg_distance,
        "sample_distances_m": [{"node_id": n, "distance_m": round(d, 1)} for n, d in sample_items],
        "distance_note": "Distances follow the road network from sampled intersections to the target, not straight-line distance.",
    }
    _analysis_cache[cache_key] = result
    return result


# ---------------------------------------------------------
# MAP VIEW — see the route on an actual map in your browser,
# no frontend framework needed.
# ---------------------------------------------------------
@app.get("/map", response_class=HTMLResponse)
def map_view(city: str, origin: str, destination: str):
    route_data = get_route(city, origin, destination)
    coords = route_data["geojson"]["geometry"]["coordinates"]
    # Leaflet wants [lat, lon] pairs, GeoJSON stores [lon, lat]
    latlngs = [[c[1], c[0]] for c in coords]

    distance_km = round(route_data["distance_m"] / 1000, 2)
    # Unique, ordered street names (skip repeats and "Unnamed road" for the panel list)
    seen = set()
    named_streets = []
    for value in route_data["streets"]:
        if isinstance(value, (list, tuple)):
            s = " / ".join(str(part) for part in value if part) or "Unnamed road"
        else:
            s = str(value) if value else "Unnamed road"
        if s not in seen and s != "Unnamed road":
            seen.add(s)
            named_streets.append(s)
    streets_html = "".join(f"<li>{html.escape(s)}</li>" for s in named_streets) or "<li>No named streets in data</li>"

    city_html = html.escape(city)
    city_js = json.dumps(city)
    latlngs_js = json.dumps(latlngs)

    page_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Route Map - {city_html}</title>
        <meta charset="utf-8" />
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <style>
            #map {{ height: 100vh; width: 100%; margin: 0; }}
            body {{ margin: 0; font-family: Arial, sans-serif; }}
            #panel {{
                position: absolute;
                top: 12px; left: 50px;
                z-index: 1000;
                background: white;
                padding: 12px 16px;
                border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.3);
                max-width: 280px;
                max-height: 80vh;
                overflow-y: auto;
            }}
            #panel h2 {{ margin: 0 0 6px 0; font-size: 16px; }}
            #panel p {{ margin: 4px 0; font-size: 13px; }}
            #panel ul {{ margin: 6px 0 0 0; padding-left: 18px; font-size: 13px; }}
        </style>
    </head>
    <body>
        <div id="panel">
            <h2>{city_html}</h2>
            <p><b>Distance:</b> {distance_km} km</p>
            <p><b>Route via:</b></p>
            <ul>{streets_html}</ul>
        </div>
        <div id="map"></div>
        <script>
            var map = L.map('map');
            L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                attribution: '&copy; OpenStreetMap contributors'
            }}).addTo(map);

            var latlngs = {latlngs_js};
            var polyline = L.polyline(latlngs, {{color: 'red', weight: 5}}).addTo(map);
            map.fitBounds(polyline.getBounds());

            L.marker(latlngs[0]).addTo(map)
                .bindPopup("<b>Start</b><br>" + {city_js}).openPopup();
            L.marker(latlngs[latlngs.length - 1]).addTo(map)
                .bindPopup("<b>Destination</b><br>Distance: {distance_km} km");
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=page_html)