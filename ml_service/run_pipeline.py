#!/usr/bin/env python3
"""CLI entry: python ml_service/run_pipeline.py --date YYYY-MM-DD --coast-id ARABIAN_SEA"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow.run import main

if __name__ == "__main__":
    raise SystemExit(main())
