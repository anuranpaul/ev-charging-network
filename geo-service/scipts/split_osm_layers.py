"""
Split a bulk OSM GeoJSON export (from Overpass Turbo or fetch_osm_data.py)
into the seven separate per-layer files the geo-service expects.

Usage:
    python scripts/split_osm_layers.py \\
        --input data/raw/bengaluru_overpass_export.geojson \\
        --city bengaluru \\
        --data-dir data
"""
import argparse
import pathlib
import sys

import geopandas as gpd

# (output filename, filter function) -- filter receives the full GeoDataFrame
# and returns a boolean mask. Using functions (not fixed column lookups) so
# a layer with zero matching rows doesn't raise a KeyError if a tag column
# is entirely absent from this particular export.
LAYER_FILTERS = {
    "ev_chargers.geojson": lambda gdf: gdf.get("amenity") == "charging_station",
    "fuel_stations.geojson": lambda gdf: gdf.get("amenity") == "fuel",
    "parking.geojson": lambda gdf: gdf.get("amenity") == "parking",
    "roads.geojson": lambda gdf: gdf.get("highway", gdf.get("highway", "")).isin(
        ["motorway", "trunk", "primary", "secondary"]
    )
    if "highway" in gdf.columns
    else gdf.index != gdf.index,  # all-False mask if column missing
    "metro_stations.geojson": lambda gdf: (gdf.get("railway") == "station")
    & (gdf.get("station") == "subway")
    if {"railway", "station"}.issubset(gdf.columns)
    else (gdf.get("station") == "subway" if "station" in gdf.columns else gdf.index != gdf.index),
    "malls.geojson": lambda gdf: gdf.get("shop") == "mall",
    "tech_parks.geojson": lambda gdf: gdf["name"].str.contains(
        r"[Tt]ech [Pp]ark|IT [Pp]ark|Business Park", na=False, regex=True
    )
    if "name" in gdf.columns
    else gdf.index != gdf.index,
}


def split(input_path: pathlib.Path, city: str, data_dir: pathlib.Path) -> None:
    print(f"Reading {input_path}...")
    gdf = gpd.read_file(input_path)
    print(f"Loaded {len(gdf)} features, columns: {list(gdf.columns)}")

    out_dir = data_dir / city
    out_dir.mkdir(parents=True, exist_ok=True)

    for filename, filter_fn in LAYER_FILTERS.items():
        mask = filter_fn(gdf)
        layer_gdf = gdf[mask].copy()

        out_path = out_dir / filename
        if len(layer_gdf) == 0:
            print(f"  WARNING: {filename} -> 0 features (writing empty file)", file=sys.stderr)
        else:
            print(f"  {filename} -> {len(layer_gdf)} features")

        # Write even if empty, so DatasetRegistry sees a present-but-empty
        # file (triggers the Req 5 AC-8 warning path) rather than a
        # missing-file 503 (Req 3 AC-3/AC-6), which is the wrong failure mode
        # for "layer legitimately has zero matches in this city."
        layer_gdf.to_file(out_path, driver="GeoJSON")

    print(f"Done. Layers written to {out_dir}/")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=pathlib.Path, required=True, help="Path to bulk OSM GeoJSON export")
    parser.add_argument("--city", required=True, help="City directory name, e.g. bengaluru (lowercase)")
    parser.add_argument("--data-dir", type=pathlib.Path, default=pathlib.Path("data"))
    args = parser.parse_args()

    split(args.input, args.city, args.data_dir)


if __name__ == "__main__":
    main()