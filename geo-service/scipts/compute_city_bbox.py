"""
Derive a city's bounding polygon by dissolving its ward boundaries.

This bounding polygon is used in two places, so it's computed once here
rather than hand-drawn twice:
  1. GET /cities boundingBox field (Requirement 4, AC-6)
  2. The clip extent for the population raster (build_population_grid.py)

Usage:
    python scripts/compute_city_bbox.py --city bengaluru --data-dir data
"""
import argparse
import json
import pathlib

import geopandas as gpd


def compute(city: str, data_dir: pathlib.Path) -> None:
    wards_path = data_dir / city / "ward_boundaries.geojson"
    if not wards_path.exists():
        raise FileNotFoundError(
            f"{wards_path} not found -- run prepare_ward_boundaries.py first."
        )

    gdf = gpd.read_file(wards_path)
    # Fix any invalid geometries in the source file before taking the union
    gdf.geometry = gdf.geometry.make_valid()
    dissolved = gdf.union_all() if hasattr(gdf, "union_all") else gdf.unary_union
    bbox_polygon = dissolved.convex_hull

    out_path = data_dir / city / "city_bbox.geojson"
    gpd.GeoDataFrame({"city": [city]}, geometry=[bbox_polygon], crs=gdf.crs).to_file(
        out_path, driver="GeoJSON"
    )

    minx, miny, maxx, maxy = bbox_polygon.bounds
    print(f"City: {city}")
    print(f"Bounding box (minx, miny, maxx, maxy): {minx:.6f}, {miny:.6f}, {maxx:.6f}, {maxy:.6f}")
    print(f"Wrote convex-hull polygon to {out_path}")
    print()
    print("Bbox tuple for build_population_grid.py --bbox argument:")
    print(f"  {minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", required=True)
    parser.add_argument("--data-dir", type=pathlib.Path, default=pathlib.Path("data"))
    args = parser.parse_args()

    compute(args.city, args.data_dir)


if __name__ == "__main__":
    main()