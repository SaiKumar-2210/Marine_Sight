# MarineSight — oil spill detection & vessel attribution

Sentinel-1 SAR oil-slick segmentation (SkyTruth Cerulean methodology), multi-modal false-positive
filtering (Sentinel-2 + MetOcean), AIS-based culprit attribution and a MarineTraffic-style
operations map.

```
                 ┌──────────────── Python ML pipeline (ml_service/marinesight) ────────────────┐
 CDSE catalog ──►│ Sentinel-1 IW GRD, VV σ0 dB @ 360/2^19° (~73 m) ─► U-Net segmentation        │
                 │   └─► OpenCV findContours (4× NN upsample + half-pixel mitre buffer)         │
                 │         = exact slick polygons (holes kept), grouped Cerulean-style          │
                 │ ONLY for SAR candidates:                                                     │
                 │   Sentinel-2 L2A (±36 h, drift-corrected) + Open-Meteo ERA5 wind,            │
                 │   Copernicus Marine currents/waves ─► gradient-boosted verifier → P(oil)     │
                 │ Attribution: AIS drift back-propagation + CFAR SAR vessels                   │
                 │   (island/platform removal via temporal persistence) + infrastructure        │
                 └───────────────────────────────▲──────────────────────────┬───────────────────┘
                   JSON-lines progress (stderr)   │ spawn per date          │ result.json
 ┌────────────────────────── Node backend (server/) ───────────────────────────────────────────┐
 │ scanJobs  single-flight per date, one pipeline at a time, stores spills in SQLite (WAL)     │
 │ scheduler daily global scan of the previous UTC day, exactly once (atomic DB claim)         │
 │ ais       one aisstream.io burst every 10 min → vessel state + AIS history (AOIs) → cached, │
 │           gzipped, ETag'd snapshot                                                          │
 │ REST + WebSocket (/api/events: scan progress, AIS refresh)                                  │
 └───────────────────────────────────────▲────────────────────────────────────────────────────┘
 ┌──────────────── React UI (src/) ──────┴─────────────────────────────────────────────────────┐
 │ Leaflet map + deck.gl WebGL AIS layer · slick polygons · SAR evidence chip · attribution    │
 │ tracks · "Loading…" with live pipeline progress for dates not yet in the database           │
 └─────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Run

```bash
cp .env.example .env            # fill in CDSE + aisstream credentials
npm install
pip install -r ml_service/requirements.txt
npm run build && npm start      # http://localhost:3000/app   (or `npm run dev` for Vite + API)
```

`GET /api/spills?date=YYYY-MM-DD` returns stored spills, or — for a date that is not in the
database — starts the ML pipeline and answers `202 {state:"loading", job}`; the UI shows
"Loading…" with live progress and renders the spills when the scan completes.

| Endpoint | Purpose |
|---|---|
| `GET /api/spills?date=` · `GET /api/spills/:id` | spills (polygon, P(oil), culprit) · full evidence |
| `GET /api/scans` · `GET /api/scans/:date` · `POST /api/scans` | scan index · job progress · forced rescan |
| `GET /api/ais/vessels` | 10-minute AIS snapshot (ETag / 304) |
| `GET /api/ais/vessels/:mmsi?hours=` | stored track for a vessel |
| `GET /api/sentinel/tiles/:collection/:z/:x/:y.png?date=` | Sentinel-1/2 basemap tiles |
| `WS /api/events` | `scan-progress`, `scan-complete`, `ais-refresh` |

Monitored areas for the daily scan live in `ml_service/aois.json`.

## Models

| Model | Training data | Script |
|---|---|---|
| U-Net (1.6 M params, VV dB + local-anomaly + mask channels) | 456 real Sentinel-1 chips; masks = SkyTruth Cerulean slick polygons (human-reviewed + high-confidence), scene-held-out validation | `training/build_dataset.py`, `training/train_unet.py` |
| Verifier (HistGradientBoosting, 24 features) | U-Net candidates on the same chips; positives = Cerulean slicks, negatives = candidates Cerulean did not publish (look-alikes); Sentinel-2 + MetOcean fetched per chip; scene-grouped CV | `training/train_verifier.py` |

The Oman benchmark scene (2026-08-07) is excluded from both training sets. Validation metrics are
stored inside the model files and reported by the pipeline.

## Tests

```bash
npm test                 # backend (stub pipeline) + Python unit tests
npm run test:benchmark   # Oman 2026-08-07 vs Cerulean reference polygon (network)
npm run test:e2e         # browser QA: fresh DB → Loading… → pipeline → DB → API → map
```

## Known limitations

- AIS history is recorded from the live aisstream feed going forward (terrestrial coverage only).
  Dates before recording started have no AIS tracks; attribution then relies on vessels visible in
  the SAR image itself. Historical AIS can be imported into the `ais_positions` / `vessels` tables.
- The supplied Kpler token is rejected by Kpler's auth server; its status is reported at
  `/api/ais/status`.
- "Global" means the monitored AOIs in `aois.json` — every Sentinel-1 pass over them each day.
