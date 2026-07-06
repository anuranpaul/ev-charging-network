"""
Validate and place a downloaded ward boundary file (e.g. datameet's
BBMP.GeoJSON) into the geo-service data directory as ward_boundaries.geojson.

Checks and fixes CRS if it's missing or not EPSG:4326 -- community-sourced
GeoJSON files sometimes have coordinates in WGS-84 but no CRS metadata, or
mislabeled metadata.

Usage:
    python scripts/prepare_ward_boundaries.py \\
        --input ~/Downloads/BBMP.GeoJSON \\
        --city bengaluru \\
        --data-dir data
"""
import argparse
import pathlib

import geopandas as gpd

EXPECTED_EPSG = 4326


def prepare(input_path: pathlib.Path, city: str, data_dir: pathlib.Path) -> None:
    print(f"Reading {input_path}...")
    gdf = gpd.read_file(input_path)
    print(f"Loaded {len(gdf)} ward features")

    if gdf.crs is None:
        print(f"No CRS found in file -- assuming EPSG:{EXPECTED_EPSG} (WGS-84) and setting it explicitly.")
        gdf = gdf.set_crs(epsg=EXPECTED_EPSG)
    elif gdf.crs.to_epsg() != EXPECTED_EPSG:
        print(f"Reprojecting from {gdf.crs} to EPSG:{EXPECTED_EPSG}...")
        gdf = gdf.to_crs(epsg=EXPECTED_EPSG)
    else:
        print(f"CRS already EPSG:{EXPECTED_EPSG}, no change needed.")

    # Quick sanity check: coordinates should be plausible lat/lon, not
    # projected metres left over from a mislabeled CRS.
    minx, miny, maxx, maxy = gdf.total_bounds
    if not (-180 <= minx <= 180 and -90 <= miny <= 90):
        raise ValueError(
            f"Bounds {gdf.total_bounds} don't look like WGS-84 degrees. "
            "The source file's CRS metadata may be wrong -- inspect manually."
        )
    print(f"Bounding box (WGS-84): {gdf.total_bounds}")

    out_dir = data_dir / city
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "ward_boundaries.geojson"
    gdf.to_file(out_path, driver="GeoJSON")
    print(f"Wrote {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=pathlib.Path, required=True)
    parser.add_argument("--city", required=True)
    parser.add_argument("--data-dir", type=pathlib.Path, default=pathlib.Path("data"))
    args = parser.parse_args()

    prepare(args.input, args.city, args.data_dir)


if __name__ == "__main__":
    main()