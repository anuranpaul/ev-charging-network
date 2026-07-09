"""
test_endpoints.py — Live smoke test for all geo-service endpoints.
Run with: py test_endpoints.py
"""
import json, sys
import urllib.request, urllib.error

BASE = "http://localhost:8001"
PASS, FAIL = 0, 0

def req(method, path, body=None, expected_status=200, label=None):
    global PASS, FAIL
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if data else {}
    rq = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(rq, timeout=60) as resp:
            status = resp.status
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        status = e.code
        try:    payload = json.loads(e.read())
        except: payload = {}

    ok = status == expected_status
    tag = "✅ PASS" if ok else "❌ FAIL"
    print(f"\n{tag}  {method} {path}  →  HTTP {status}  (expected {expected_status})")
    if label:
        print(f"     {label}")
    if not ok:
        print(f"     body: {json.dumps(payload)[:200]}")
        FAIL += 1
    else:
        PASS += 1
    return payload, ok


# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("GET /health")
print("=" * 60)
p, _ = req("GET", "/health", label="liveness — always 200")
print(f"     {p}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("GET /ready")
print("=" * 60)
p, _ = req("GET", "/ready", label="readiness — 200 after warm")
print(f"     status={p.get('status')}  warmed_cities={p.get('warmed_cities')}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("GET /data-health")
print("=" * 60)
p, ok = req("GET", "/data-health", label="all 9 datasets loaded, bengaluru available")
if ok:
    avail = p.get("city_availability", {})
    print(f"     city_availability: {avail}")
    datasets = p.get("datasets", {})
    for name, info in list(datasets.items())[:3]:
        print(f"     {name}: count={info['record_count']} status={info['status']}")
    print(f"     ... ({len(datasets)} datasets total)")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("GET /analysis — valid request")
print("=" * 60)
p, ok = req("GET", "/analysis?city=Bengaluru&chargerType=DC_FAST",
            label="score stats + coverage + ward breakdown")
if ok:
    print(f"     total_candidates : {p['total_candidates']}")
    print(f"     score mean/med/p90: {p['score_mean']} / {p['score_median']} / {p['score_p90']}")
    print(f"     coverage_pct      : {p['coverage_pct']}%")
    print(f"     ward_stats count  : {len(p['ward_stats'])} wards")
    if p['ward_stats']:
        top = p['ward_stats'][0]
        print(f"     top ward          : {top['ward_name']}  candidates={top['candidate_count']}  mean_score={top['mean_score']}")

print("\n--- /analysis: unsupported city → 422 ---")
req("GET", "/analysis?city=Sydney&chargerType=DC_FAST", expected_status=422,
    label="city='Sydney' → 422 with message")

print("\n--- /analysis: bad chargerType → 422 ---")
req("GET", "/analysis?city=Bengaluru&chargerType=TURBO", expected_status=422,
    label="chargerType='TURBO' → 422")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("POST /validate — valid FeatureCollection")
print("=" * 60)
valid_fc = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [77.59, 12.97]}, "properties": {}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [77.61, 12.93]}, "properties": {}},
    ]
}
p, ok = req("POST", "/validate", body=valid_fc,
            label="2 valid Point features → record_count=2, no errors")
if ok:
    print(f"     record_count     : {p['record_count']}")
    print(f"     crs              : {p['crs']}")
    print(f"     geometry_types   : {p['geometry_types']}")
    print(f"     validation_errors: {p['validation_errors']}")

print("\n--- /validate: null geometry ---")
null_geom_fc = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "geometry": None, "properties": {}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [77.59, 12.97]}, "properties": {}},
    ]
}
p, ok = req("POST", "/validate", body=null_geom_fc,
            label="feature[0] has null geometry → 1 validation error at index 0")
if ok:
    print(f"     validation_errors: {p['validation_errors']}")

print("\n--- /validate: coordinate out of WGS-84 bounds ---")
oob_fc = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [999.0, 12.97]}, "properties": {}},
    ]
}
p, ok = req("POST", "/validate", body=oob_fc,
            label="lon=999 → out-of-bounds validation error")
if ok:
    print(f"     validation_errors: {p['validation_errors']}")

print("\n--- /validate: not a FeatureCollection → 400 ---")
req("POST", "/validate", body={"type": "Feature", "geometry": None, "properties": {}},
    expected_status=400, label="type='Feature' → 400")

print("\n--- /validate: invalid JSON → 400 ---")
url = BASE + "/validate"
import urllib.request as ur
rq = ur.Request(url, data=b"not json at all", headers={"Content-Type": "application/json"}, method="POST")
try:
    with ur.urlopen(rq, timeout=10) as r:
        code = r.status
        body = json.loads(r.read())
except urllib.error.HTTPError as e:
    code = e.code
    try:    body = json.loads(e.read())
    except: body = {}
ok = code == 400
tag = "✅ PASS" if ok else "❌ FAIL"
print(f"\n{tag}  POST /validate  →  HTTP {code}  (expected 400)")
print(f"     raw bytes 'not json at all' → 400  message={body.get('message','')[:80]}")
if ok: PASS += 1
else: FAIL += 1

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("POST /recommendation (regression check)")
print("=" * 60)
p, ok = req("POST", "/recommendation",
            body={"city": "Bengaluru", "chargerType": "DC_FAST", "radius": 1500},
            label="still working after adding new routers")
if ok:
    print(f"     total_candidates: {p['total_candidates']}")
    scores = [f["properties"]["score"] for f in p["features"]]
    print(f"     score range: {min(scores)}–{max(scores)}, mean={sum(scores)/len(scores):.1f}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"RESULTS: {PASS} passed, {FAIL} failed")
print("=" * 60)
sys.exit(1 if FAIL else 0)
