# Geo-Service

FastAPI-based geospatial service for the EV Charging Network project. Provides city-level geospatial layers (EV chargers, roads, metro stations, etc.) and a candidate-scoring API used by the Go API layer.

---

## Prerequisites

- Python 3.11+
- A virtual environment at `.venv/` (already set up in this repo)
- `rasterio` installed in the venv (`python -m pip install rasterio`)
- **External data files you must obtain manually:**
  - `BBMP.geojson` — Bengaluru ward boundaries (available from [datameet/maps](https://github.com/datameet/municipal-profiles))
  - `IND_ppp_2020_adj_v2.tif` — India WorldPop population raster (download from [worldpop.org](https://www.worldpop.org))

---

## Data Pipeline — Run Order

Run these scripts **in order** from the `geo-service/` directory. Each step's output feeds the next.

### Step 1 — Fetch OSM data

```bash
python scipts/fetch_osm_data.py
```

**What it does:**  
Queries the Overpass API for Bengaluru POIs (EV chargers, fuel stations, parking, roads, metro stations, malls, tech parks) and saves:
- Raw Overpass JSON → `data/raw/bengaluru_overpass_raw.json`
- Converted GeoJSON → `data/raw/bengaluru_overpass_export.geojson`

> If `bengaluru_overpass_raw.json` already exists it is reused — no repeat API call is made.

---

### Step 2 — Prepare ward boundaries

```bash
python scipts/fetch_ward_boundaries.py \
    --input ~/Downloads/BBMP.geojson \
    --city bengaluru \
    --data-dir data
```

**What it does:**  
Validates and reprojects the raw BBMP ward boundary file to EPSG:4326 and copies it into the data directory.

**Output:** `data/bengaluru/ward_boundaries.geojson`

---

### Step 3 — Compute city bounding box

```bash
python scipts/compute_city_bbox.py \
    --city bengaluru \
    --data-dir data
```

**What it does:**  
Dissolves all ward polygons into a single convex-hull bounding polygon. The printed `--bbox` value is used in the next step.

**Output:** `data/bengaluru/city_bbox.geojson`

> Copy the printed bbox tuple (e.g. `77.459880,12.833490,77.784361,13.142620`) for use in Step 5.

---

### Step 4 — Split OSM layers

```bash
python scipts/split_osm_layers.py \
    --input data/raw/bengaluru_overpass_export.geojson \
    --city bengaluru \
    --data-dir data
```

**What it does:**  
Reads the GeoJSON export from Step 1 and filters it into seven separate per-layer files the geo-service API expects.

**Output (in `data/bengaluru/`):**

| File | Content |
|---|---|
| `ev_chargers.geojson` | EV charging stations |
| `fuel_stations.geojson` | Petrol / CNG stations |
| `parking.geojson` | Parking areas |
| `roads.geojson` | Motorway / trunk / primary / secondary roads |
| `metro_stations.geojson` | Metro / subway stations |
| `malls.geojson` | Shopping malls |
| `tech_parks.geojson` | IT and tech parks |

---

### Step 5 — Build population grid

```bash
python scipts/build_population_grid.py \
    --raster ~/Downloads/IND_ppp_2020_adj_v2.tif \
    --bbox 77.459880,12.833490,77.784361,13.142620 \
    --city bengaluru \
    --data-dir data \
    --buffer-km 3
```

**What it does:**  
Clips the India-wide WorldPop population raster to the city bbox, vectorises populated pixels into GeoJSON polygons, and writes the result.

**Output:** `data/bengaluru/population_grid.geojson`

> Use the `--bbox` value printed at the end of Step 3.

---

## Running the API Server

```bash
uvicorn app.main:app --reload
```

The server starts at `http://localhost:8000`. Interactive docs available at `http://localhost:8000/docs`.

---

## Running Tests

```bash
pytest
```

---

## Final data directory layout

After running all steps, `data/bengaluru/` should contain:

```
data/
├── raw/
│   ├── bengaluru_overpass_raw.json        # Step 1 — raw Overpass download
│   └── bengaluru_overpass_export.geojson  # Step 1 — converted GeoJSON
└── bengaluru/
    ├── ward_boundaries.geojson            # Step 2
    ├── city_bbox.geojson                  # Step 3
    ├── ev_chargers.geojson                # Step 4
    ├── fuel_stations.geojson              # Step 4
    ├── parking.geojson                    # Step 4
    ├── roads.geojson                      # Step 4
    ├── metro_stations.geojson             # Step 4
    ├── malls.geojson                      # Step 4
    ├── tech_parks.geojson                 # Step 4
    └── population_grid.geojson            # Step 5
```
