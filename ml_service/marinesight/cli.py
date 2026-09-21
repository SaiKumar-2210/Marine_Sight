"""Command-line entry used by the Node job runner.

    python -m marinesight.cli scan --date 2026-08-07 --ais-db ../marinesight.sqlite --out result.json
    python -m marinesight.cli scan --date 2026-08-07 --aoi OMAN_ARABIAN_SEA
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="marinesight")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="run the full pipeline for one UTC date")
    s.add_argument("--date", required=True)
    s.add_argument("--aoi", action="append", help="restrict to AOI id(s); default all")
    s.add_argument("--bbox", help="custom west,south,east,north instead of AOIs")
    s.add_argument("--ais-db", default=None)
    s.add_argument("--out", default=None)
    ns = ap.parse_args(argv)

    from .pipeline import emit, scan

    try:
        bbox = [float(v) for v in ns.bbox.split(",")] if ns.bbox else None
        result = scan(ns.date, ns.aoi, ns.ais_db, bbox)
    except Exception as exc:  # report failure as data so the job runner can store it
        traceback.print_exc()
        result = {"ok": False, "date": ns.date, "error": str(exc)}
        emit("error", message=str(exc))
    text = json.dumps(result, default=str)
    if ns.out:
        Path(ns.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
