"""End-to-end QA: Python ML pipeline -> SQLite -> Node API -> React/Leaflet UI.

Starts the real server on a fresh database, opens the operations UI for the benchmark date
(2026-08-07, not yet in the DB) and verifies, in the browser:
  1. the on-demand fallback shows "Loading…" with live pipeline progress,
  2. the pipeline's spills land in the DB and render as polygons whose on-screen geometry
     matches the stored coordinates,
  3. the inspector shows verification + attribution, the evidence imagery loads,
  4. the WebGL AIS layer renders live vessels that can be picked,
  5. a reload is served straight from the DB (no second pipeline run).

    python scripts/e2e_qa.py            (uses the system Chrome; artefacts in qa-artifacts/)
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "qa-artifacts"
PORT = int(os.environ.get("QA_PORT", 3055))
BASE = f"http://localhost:{PORT}"
DATE = os.environ.get("QA_DATE", "2026-08-07")
CHROME = os.environ.get("QA_CHROME", r"C:\Program Files\Google\Chrome\Application\chrome.exe")

checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{' — ' + detail if detail else ''}", flush=True)


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return r.status, json.loads(r.read() or b"null"), dict(r.headers)


def main() -> int:
    OUT.mkdir(exist_ok=True)
    db_path = OUT / "qa.sqlite"
    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    if "--no-build" not in sys.argv:
        print("[qa] building frontend", flush=True)
        subprocess.run("npm run build", cwd=ROOT, shell=True, check=True, stdout=subprocess.DEVNULL)

    env = {**os.environ, "PORT": str(PORT), "MARINESIGHT_DB": str(db_path), "SCHEDULER_DISABLED": "1",
           "AIS_BURST_SECONDS": os.environ.get("AIS_BURST_SECONDS", "45")}
    log = open(OUT / "server.log", "w", encoding="utf-8")
    server = subprocess.Popen(["node", "server.js"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            try:
                get("/api/health")
                break
            except Exception:
                time.sleep(1)
        status, health, _ = get("/api/health")
        check("API health", status == 200 and health["ok"], json.dumps(health["integrations"]))
        check("ML models present", health["integrations"]["mlModelsReady"])

        print("[qa] waiting for first AIS burst", flush=True)
        vessels = 0
        for _ in range(40):
            _, st, _ = get("/api/ais/status")
            vessels = st.get("vessels", 0)
            if st.get("lastRefreshAt") and vessels:
                break
            time.sleep(3)
        check("AIS cache populated from aisstream", vessels > 0, f"{vessels} vessels")
        _, snap, headers = get("/api/ais/vessels")
        check("AIS cache advertises 10-minute refresh", snap["refreshIntervalSec"] == 600 and "ETag" in headers,
              f"max-age {headers.get('Cache-Control')}")

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROME, headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1600, "height": 950})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

            print(f"[qa] opening UI for {DATE} (not in DB yet)", flush=True)
            page.goto(f"{BASE}/app?date={DATE}")
            loading = page.locator("[data-testid=scan-loading]")
            loading.wait_for(state="visible", timeout=20000)
            check("On-demand fallback shows Loading…", "Loading" in loading.inner_text())
            page.screenshot(path=str(OUT / "01_loading.png"))
            messages, t0 = set(), time.time()
            while loading.is_visible() and time.time() - t0 < 40 * 60:
                messages.add(loading.locator("small").inner_text())
                time.sleep(2)
            check("Pipeline progress streamed to UI", len(messages) >= 3, f"{len(messages)} distinct stage messages")
            check("Scan finished in the UI", not loading.is_visible() and not page.locator("[data-testid=scan-failed]").is_visible(),
                  f"{time.time() - t0:.0f}s")

            _, api, _ = get(f"/api/spills?date={DATE}")
            spills = api["spills"]
            check("API returns spills for the date", api["state"] == "ready" and len(spills) >= 1, f"{len(spills)} spills")
            con = sqlite3.connect(db_path)
            rows = con.execute("SELECT id, geometry_json, status FROM spills WHERE date = ? AND status != 'rejected'", (DATE,)).fetchall()
            scan_row = con.execute("SELECT status, trigger FROM scans WHERE date = ?", (DATE,)).fetchone()
            con.close()
            check("Spills persisted in SQLite", len(rows) == len(spills) and scan_row == ("complete", "on-demand"), f"scan={scan_row}")
            same = all(json.loads(g) == next(s["geometry"] for s in spills if s["id"] == i) for i, g, _ in rows)
            check("DB polygons identical to API polygons", same)

            page.wait_for_selector("[data-testid=spill-row]", timeout=20000)
            n_rows = page.locator("[data-testid=spill-row]").count()
            check("Detections listed in UI queue", n_rows == len(spills), f"{n_rows} rows")
            n_paths = page.locator("path.slick-polygon").count()
            check("Slick polygons rendered on map", n_paths == len(spills), f"{n_paths} SVG polygons")

            big = max(spills, key=lambda s: s["areaKm2"])
            page.locator("[data-testid=spill-row]", has_text=big["id"]).click()
            page.wait_for_timeout(2500)
            # Screen-space check: projected stored coordinates vs the rendered SVG path box.
            cmp = page.evaluate("""(geom) => {
                const map = document.getElementById('map').__msMap;
                const pts = geom.coordinates.flat(2).map(([lon, lat]) => map.latLngToContainerPoint([lat, lon]));
                const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
                const want = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
                const mapBox = document.getElementById('map').getBoundingClientRect();
                let best = null;
                for (const el of document.querySelectorAll('path.slick-polygon')) {
                  const r = el.getBoundingClientRect();
                  const got = [r.left - mapBox.left, r.top - mapBox.top, r.right - mapBox.left, r.bottom - mapBox.top];
                  const err = Math.max(...got.map((v, i) => Math.abs(v - want[i])));
                  if (best === null || err < best.err) best = { err, got, want };
                }
                return best;
            }""", big["geometry"])
            check("Rendered polygon matches stored coordinates (screen space)", cmp and cmp["err"] <= 4,
                  f"max edge error {cmp and round(cmp['err'], 2)} px")
            page.screenshot(path=str(OUT / "02_spill_selected.png"))

            insp = page.locator("[data-testid=inspector]")
            prob = page.locator("[data-testid=oil-probability]").inner_text()
            check("Inspector shows verifier P(oil)", prob.endswith("%") and prob == f"{round(big['oilProbability'] * 100)}%", prob)
            page.locator("[data-testid=tab-verification]").click()
            check("Verification tab shows MetOcean + S2", "WIND" in insp.inner_text() and "SENTINEL-2" in insp.inner_text())
            page.locator("[data-testid=tab-attribution]").click()
            page.wait_for_timeout(500)
            att_text = page.locator("[data-testid=attribution-pane]").inner_text()
            n_cand = page.locator("[data-testid=candidate]").count()
            check("Attribution tab lists ranked sources", n_cand >= 1 or "No vessel track" in att_text, f"{n_cand} candidates")
            page.screenshot(path=str(OUT / "03_attribution.png"))

            page.locator("button", has_text="Evidence").first.click()
            modal = page.locator("[data-testid=evidence-modal]")
            modal.wait_for(state="visible", timeout=10000)
            page.wait_for_timeout(2500)
            imgs = page.evaluate("() => [...document.querySelectorAll('[data-testid=evidence-modal] img')].map(i => i.naturalWidth)")
            check("Evidence modal loads real S1 (and S2) imagery", len(imgs) >= 1 and all(w > 0 for w in imgs), f"widths {imgs}")
            page.screenshot(path=str(OUT / "04_evidence.png"))
            page.keyboard.press("Escape")

            # AIS layer: fly to a busy area, pick a vessel by clicking its projected position.
            _, snap, _ = get("/api/ais/vessels")
            idx = {f: i for i, f in enumerate(snap["fields"])}
            moving = [r for r in snap["rows"] if (r[idx["sog"]] or 0) > 3]
            target = moving[0] if moving else snap["rows"][0]
            page.evaluate("([lat, lon]) => document.getElementById('map').__msMap.setView([lat, lon], 9, {animate: false})",
                          [target[idx["lat"]], target[idx["lon"]]])
            page.wait_for_timeout(2500)
            pt = page.evaluate("([lat, lon]) => { const m = document.getElementById('map').__msMap; const p = m.latLngToContainerPoint([lat, lon]); const b = document.getElementById('map').getBoundingClientRect(); return [p.x + b.left, p.y + b.top]; }",
                               [target[idx["lat"]], target[idx["lon"]]])
            page.mouse.move(pt[0], pt[1])
            page.wait_for_timeout(400)
            page.mouse.click(pt[0], pt[1])
            card = page.locator("[data-testid=vessel-card]")
            try:
                card.wait_for(state="visible", timeout=5000)
                text = card.inner_text()
                check("WebGL AIS vessel rendered and pickable", target[idx["mmsi"]] in text,
                      f"clicked MMSI {target[idx['mmsi']]}; card: {' / '.join(text.splitlines()[1:4])}")
            except Exception:
                check("WebGL AIS vessel rendered and pickable", False, f"no vessel card at {pt}")
            footer = page.locator("[data-testid=footer-status]").inner_text()
            m = re.search(r"AIS\s*([\d,]+)\s*vessels", footer)
            check("UI shows live AIS vessel count", bool(m) and int(m.group(1).replace(",", "")) > 0, footer.replace("\n", " "))
            page.screenshot(path=str(OUT / "05_ais_layer.png"))

            # Imagery date label: names the acquisition actually drawn, not the requested date.
            badge = page.locator("[data-testid=imagery-date]")
            check("Imagery date label is on screen", badge.is_visible(), badge.inner_text().replace("\n", " · "))
            page.click("button.map-mode:has-text('Sentinel-1')")
            page.wait_for_timeout(5000)
            label = page.locator("[data-testid=imagery-date-value]").inner_text()
            cov = page.evaluate(
                "fetch('/api/sentinel/coverage?collection=sentinel-1-grd&date=" + DATE + "&bbox=' +"
                " (() => {const b = document.getElementById('map').__msMap.getBounds();"
                " return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].join(',');})()).then(r => r.json())")
            acquired = (cov or {}).get("acquiredAt", "")
            check("Label names the real Sentinel-1 acquisition",
                  "Sentinel-1" in label and acquired[:4] in label, f"{label!r} vs scene {acquired}")
            check("Lookback for imagery is capped at 5 days",
                  (cov or {}).get("maxLookbackDays") == 5, json.dumps(cov)[:160])

            # Sentinel-2 view: the SAR raster must not be painted over the optical imagery.
            page.click("button.map-mode:has-text('Sentinel-2')")
            page.wait_for_timeout(6000)
            rasters = page.evaluate("document.querySelectorAll('.leaflet-overlay-pane img').length")
            s1_tiles = page.evaluate(
                "[...document.querySelectorAll('.leaflet-tile-pane img')].filter(i => i.src.includes('sentinel-1-grd')).length")
            s2_tiles = page.evaluate(
                "[...document.querySelectorAll('.leaflet-tile-pane img')].filter(i => i.src.includes('sentinel-2-l2a')).length")
            check("Sentinel-1 layers are cleared in the Sentinel-2 view",
                  rasters == 0 and s1_tiles == 0 and s2_tiles > 0, f"sar rasters {rasters}, s1 tiles {s1_tiles}, s2 tiles {s2_tiles}")
            page.screenshot(path=str(OUT / "05b_sentinel2.png"))
            page.click("button.map-mode:has-text('Operations')")
            page.wait_for_timeout(2000)

            # Date selection: stepping a day updates the view without an error.
            page.click('button[aria-label="Previous day"]')
            page.wait_for_timeout(2500)
            prev_day = (datetime.date.fromisoformat(DATE) - datetime.timedelta(days=1)).isoformat()
            check("Day step switches the date cleanly",
                  page.input_value("[data-testid=date-input]") == prev_day
                  and not page.locator("[data-testid=scan-failed]").is_visible(),
                  page.input_value("[data-testid=date-input]"))
            page.fill("[data-testid=date-input]", "2031-01-01")
            page.wait_for_timeout(1200)
            check("Impossible date is refused in the UI, not as a failed scan",
                  page.locator("[data-testid=date-error]").is_visible()
                  and not page.locator("[data-testid=scan-failed]").is_visible(),
                  page.locator("[data-testid=date-error]").inner_text() if page.locator("[data-testid=date-error]").count() else "no message")

            # Persistence: reload -> served from DB, no Loading overlay.
            t1 = time.time()
            page.goto(f"{BASE}/app?date={DATE}")
            page.wait_for_selector("[data-testid=spill-row]", timeout=15000)
            check("Reload served from DB without re-running the pipeline",
                  not page.locator("[data-testid=scan-loading]").is_visible(), f"{time.time() - t1:.1f}s")
            page.locator("[data-testid=spill-row]", has_text=big["id"]).click()
            page.wait_for_timeout(2500)
            page.screenshot(path=str(OUT / "06_final.png"))

            real_errors = [e for e in errors if "favicon" not in e and "tile" not in e.lower()]
            check("No JavaScript errors in the browser", not real_errors, "; ".join(real_errors[:3]))
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(10)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()

    failed = [c for c in checks if not c[1]]
    (OUT / "qa_report.json").write_text(json.dumps([{"check": n, "pass": ok, "detail": d} for n, ok, d in checks], indent=2))
    print(f"\n[qa] {len(checks) - len(failed)}/{len(checks)} checks passed; artefacts in {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
