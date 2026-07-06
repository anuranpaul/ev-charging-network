"""
Clip the India-wide WorldPop population raster to a city's bounding box
and vectorize it into population_grid.geojson (one polygon per pixel,
with a `population` attribute), matching the layer the Scorer's
population factor joins against.

Run compute_city_bbox.py first to get the --bbox value, or pass your own.

Usage:
    python scripts/build_population_grid.py \\
        --raster ~/Downloads/IND_ppp_2020_adj_v2.tif \\
        --bbox 77.35,12.75,77.85,13.15 \\
        --city bengaluru \\
        --data-dir data \\
        --buffer-km 3
"""
import argparse
import pathlib

import geopandas as gpd
import numpy as np
import rasterio
import rasterio.mask
from rasterio.features import shapes
from shapely.geometry import box, shape


def build(
    raster_path: pathlib.Path,
    bbox: tuple[float, float, float, float],
    city: str,
    data_dir: pathlib.Path,
    buffer_km: float,
) -> None:
    minx, miny, maxx, maxy = bbox

    # Pad the bbox slightly so population within the search radius near the
    # city's edge isn't undercounted by clipping too tightly. 1 degree of
    # latitude is ~111km, so buffer_km / 111 gives a rough degree offset.
    pad = buffer_km / 111.0
    padded_bbox = box(minx - pad, miny - pad, maxx + pad, maxy + pad)

    print(f"Opening {raster_path}...")
    with rasterio.open(raster_path) as src:
        print(f"Source raster CRS: {src.crs}, shape: {src.shape}")

        out_image, out_transform = rasterio.mask.mask(
            src, [padded_bbox], crop=True, nodata=src.nodata
        )
        out_meta = src.meta.copy()
        nodata_value = src.nodata

    band = out_image[0]
    print(f"Clipped raster shape: {band.shape}")

    # Mask out nodata / zero-population cells before vectorizing -- these
    # are overwhelmingly the majority of any raster (unsettled land) and
    # vectorizing them would produce a huge, useless file.
    valid_mask = band != nodata_value if nodata_value is not None else ~np.isnan(band)
    valid_mask &= band > 0

    print(f"Valid (populated) pixels: {valid_mask.sum()} of {band.size}")
    if valid_mask.sum() == 0:
        raise ValueError(
            "No populated pixels found in the clipped area. "
            "Check that --bbox is correct and overlaps the city."
        )

    records = []
    for geom, value in shapes(band, mask=valid_mask, transform=out_transform):
        records.append({"population": float(value), "geometry": shape(geom)})

    print(f"Vectorized into {len(records)} grid-cell polygons")

    gdf = gpd.GeoDataFrame(records, crs=out_meta.get("crs") or "EPSG:4326")
    if gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    out_dir = data_dir / city
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "population_grid.geojson"
    gdf.to_file(out_path, driver="GeoJSON")

    print(f"Wrote {out_path}")
    print(f"Total population in clipped area: {gdf['population'].sum():,.0f}")


def parse_bbox(s: str) -> tuple[float, float, float, float]:
    parts = tuple(float(x) for x in s.split(","))
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bbox must be minx,miny,maxx,maxy")
    return parts  # type: ignore[return-value]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raster", type=pathlib.Path, required=True, help="Path to IND_ppp_*.tif")
    parser.add_argument(
        "--bbox",
        type=parse_bbox,
        required=True,
        help="minx,miny,maxx,maxy in WGS-84 degrees (get this from compute_city_bbox.py)",
    )
    parser.add_argument("--city", required=True)
    parser.add_argument("--data-dir", type=pathlib.Path, default=pathlib.Path("data"))
    parser.add_argument(
        "--buffer-km",
        type=float,
        default=3.0,
        help="Padding around the bbox in km, so edge-of-city candidates don't undercount population (default 3km)",
    )
    args = parser.parse_args()

    build(args.raster, args.bbox, args.city, args.data_dir, args.buffer_km)


if __name__ == "__main__":
    main()