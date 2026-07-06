# scripts/fetch_osm_data.py
import json
import os

import geopandas as gpd
import requests
from shapely.geometry import LineString, Point, Polygon

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

QUERY = """
[out:json][timeout:120];
area["name"="Bengaluru"]->.searchArea;
(
  node["amenity"="charging_station"](area.searchArea);
  way["amenity"="charging_station"](area.searchArea);
  node["amenity"="fuel"](area.searchArea);
  way["amenity"="fuel"](area.searchArea);
  way["amenity"="parking"](area.searchArea);
  way["highway"~"^(motorway|trunk|primary|secondary)$"](area.searchArea);
  node["railway"="station"]["station"="subway"](area.searchArea);
  node["station"="subway"](area.searchArea);
  node["shop"="mall"](area.searchArea);
  way["shop"="mall"](area.searchArea);
  way["landuse"="commercial"]["name"~"[Tt]ech [Pp]ark|IT [Pp]ark|Business Park"](area.searchArea);
);
out body;
>;
out skel qt;
"""

def fetch() -> None:
    raw_path = "data/raw/bengaluru_overpass_raw.json"
    geojson_path = "data/raw/bengaluru_overpass_export.geojson"
    
    os.makedirs("data/raw", exist_ok=True)
    
    if os.path.exists(raw_path):
        print(f"Loading existing raw OSM data from {raw_path}...")
        with open(raw_path) as f:
            data = json.load(f)
    else:
        print("Fetching OSM data from Overpass API...")
        headers = {"User-Agent": "EVChargingNetworkProject/1.0 (contact: ev-charging-network@example.com)"}
        response = requests.post(OVERPASS_URL, data={"data": QUERY}, headers=headers, timeout=180)
        response.raise_for_status()
        data = response.json()
        with open(raw_path, "w") as f:
            json.dump(data, f)
        print(f"Saved raw OSM data with {len(data.get('elements', []))} elements")

    elements = data.get("elements", [])
    
    # 1. Map node IDs to coordinates
    nodes_coords = {}
    for elem in elements:
        if elem.get("type") == "node":
            nodes_coords[elem["id"]] = (elem["lon"], elem["lat"])

    # 2. Build features
    features = []
    for elem in elements:
        elem_type = elem.get("type")
        tags = elem.get("tags")
        if not tags:
            # Skip untagged elements (they are just skeleton nodes)
            continue
        
        geom = None
        if elem_type == "node":
            geom = Point(elem["lon"], elem["lat"])
        elif elem_type == "way":
            way_nodes = elem.get("nodes", [])
            coords = [nodes_coords[nid] for nid in way_nodes if nid in nodes_coords]
            if len(coords) < 2:
                continue
            if len(coords) >= 4 and coords[0] == coords[-1]:
                geom = Polygon(coords)
            else:
                geom = LineString(coords)
                
        if geom is not None:
            properties = {"id": elem["id"], "osm_type": elem_type}
            properties.update(tags)
            features.append({"geometry": geom, **properties})

    if features:
        gdf = gpd.GeoDataFrame(features, crs="EPSG:4326")
        gdf.to_file(geojson_path, driver="GeoJSON")
        print(f"Converted and saved {len(gdf)} features to {geojson_path}")
    else:
        print("No tagged features found to save.")

if __name__ == "__main__":
    fetch()