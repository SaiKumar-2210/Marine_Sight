#!/usr/bin/env python3
"""
MarineSight - Automated QA Verification Test Suite
Tests every core feature against strict PRD and quality guidelines:
1. System API & Integration Health
2. Multi-Coast Operational Geographic Region Selector
3. SQLite AIS Snapshot Database & Zoom LOD Priority Matrix
4. Ocean Land Masking Compute Optimization Filter
5. PyTorch UNet CNN Dark Slick Model Inference Execution
6. Targeted AOI Analysis, Cloud Fallback & Hydrodynamic Drift Formula
7. Multi-Factor Composite Confidence Score Calculation (0-100%)
"""

import os
import sys
import json
import urllib.request
import urllib.error
import subprocess
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE_URL = "http://localhost:3000"

def log_qa(test_name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {test_name}: {detail}")
    return passed

def http_get(path):
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "MarineSight-QA-Tester/1.0"})
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read().decode('utf-8'))

def http_post(path, data):
    url = f"{BASE_URL}{path}"
    body = json.dumps(data).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "MarineSight-QA-Tester/1.0"})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode('utf-8'))

def run_qa_suite():
    print("==================================================================")
    print("        MarineSight Automated QA Verification Test Suite          ")
    print("==================================================================")

    results = []

    # ------------------------------------------------------------------
    # Test 1: API & Integration Health Check
    # ------------------------------------------------------------------
    try:
        status, health = http_get("/api/health")
        passed = (status == 200 and health.get("ok") is True and health.get("integrations", {}).get("snapshotDbReady") is True)
        results.append(log_qa("Test 1: API & Integration Health", passed, f"Status: {status}, Active Coast: {health.get('activeCoast')}"))
    except Exception as e:
        results.append(log_qa("Test 1: API & Integration Health", False, f"Connection failed: {e}"))

    # ------------------------------------------------------------------
    # Test 2: Multi-Coast Region Selector API
    # ------------------------------------------------------------------
    try:
        status, coasts_data = http_get("/api/coasts")
        coasts_count = len(coasts_data.get("coasts", []))
        
        # Test switching coast to Gulf of Mexico
        select_status, select_res = http_post("/api/coasts/select", {"id": "GULF_OF_MEXICO"})
        passed = (status == 200 and coasts_count >= 5 and select_status == 200 and select_res.get("success") is True)
        results.append(log_qa("Test 2: Multi-Coast Region Selector", passed, f"Available Coasts: {coasts_count}, Selected: {select_res.get('activeCoast', {}).get('name')}"))
    except Exception as e:
        results.append(log_qa("Test 2: Multi-Coast Region Selector", False, str(e)))

    # ------------------------------------------------------------------
    # Test 3: SQLite AIS Snapshot Database & Zoom LOD Priority Matrix
    # ------------------------------------------------------------------
    try:
        # Zoom 4 => Priority 1 Only
        status_z4, snapshot_z4 = http_get("/api/ais/snapshot?zoom=4")
        max_p_z4 = snapshot_z4.get("maxPriorityFilter")
        
        # Zoom 12 => Priority 3 Allowed
        status_z12, snapshot_z12 = http_get("/api/ais/snapshot?zoom=12")
        max_p_z12 = snapshot_z12.get("maxPriorityFilter")

        passed = (status_z4 == 200 and max_p_z4 == 1 and status_z12 == 200 and max_p_z12 == 3)
        results.append(log_qa("Test 3: SQLite AIS Snapshot DB & LOD Matrix", passed, f"Zoom 4 Priority Filter: {max_p_z4}, Zoom 12 Priority Filter: {max_p_z12}"))
    except Exception as e:
        results.append(log_qa("Test 3: SQLite AIS Snapshot DB & LOD Matrix", False, str(e)))

    # ------------------------------------------------------------------
    # Test 4: Ocean Land Masking Compute Optimization Filter
    # ------------------------------------------------------------------
    try:
        # Bounding box strictly inside Central India landmass
        inland_bbox = [76.0, 21.0, 78.0, 23.0]
        status_land, res_land = http_post("/api/sentinel/process-tile", {"collection": "sentinel-1-grd", "bbox": inland_bbox})
        passed = (status_land == 422 and "Land Mask" in res_land.get("error", ""))
        results.append(log_qa("Test 4: Ocean Land Masking Compute Filter", passed, f"Status: {status_land}, Rejected: {res_land.get('error')}"))
    except Exception as e:
        results.append(log_qa("Test 4: Ocean Land Masking Compute Filter", False, str(e)))

    # ------------------------------------------------------------------
    # Test 5: Direct Execution of UNet CNN Dark Slick Model
    # ------------------------------------------------------------------
    try:
        script_path = os.path.join("ml_service", "sar_cnn_slick_detector.py")
        proc = subprocess.run([sys.executable, script_path, "18.57", "71.88"], capture_output=True, text=True, timeout=15)
        ml_out = json.loads(proc.stdout)
        passed = (proc.returncode == 0 and ml_out.get("darkSlickDetected") is True and ml_out.get("sarCnnModelScore", 0) > 80.0)
        results.append(log_qa("Test 5: UNet CNN SAR Dark Slick Inference", passed, f"Model: {ml_out.get('modelName')}, Score: {ml_out.get('sarCnnModelScore')}%, Slick Area: {ml_out.get('slickAreaSqKm')} km2"))
    except Exception as e:
        results.append(log_qa("Test 5: UNet CNN SAR Dark Slick Inference", False, str(e)))

    # ------------------------------------------------------------------
    # Test 6: Targeted AOI Analysis, Cloud Fallback & Hydrodynamic Drift
    # ------------------------------------------------------------------
    try:
        status_aoi, aoi_res = http_post("/api/incidents/analyze-aoi", {"lat": 18.57, "lng": 71.88, "incidentId": "INC-040"})
        conf = aoi_res.get("confidenceScore", 0)
        fallback = aoi_res.get("opticalCloudFallback", {}).get("triggered")
        drift_formula = aoi_res.get("hydrodynamicDrift", {}).get("driftFormula")

        passed = (status_aoi == 200 and conf >= 80.0 and fallback is True and "V_slick" in drift_formula)
        results.append(log_qa("Test 6: Targeted AOI Multi-Modal Analysis", passed, f"Confidence: {conf}%, Cloud Fallback Triggered: {fallback}, Formula: {drift_formula}"))
    except Exception as e:
        results.append(log_qa("Test 6: Targeted AOI Multi-Modal Analysis", False, str(e)))

    # ------------------------------------------------------------------
    # Test 7: Multi-Factor Composite Confidence Score Calculation
    # ------------------------------------------------------------------
    try:
        status_aoi, aoi_res = http_post("/api/incidents/analyze-aoi", {"lat": 18.57, "lng": 71.88, "incidentId": "INC-040"})
        confidence = aoi_res.get("confidenceScore")
        breakdown = aoi_res.get("signalsBreakdown", {})
        sar = breakdown.get("sarCnnModelScore", 0.0)
        optical = breakdown.get("opticalVerificationScore", 0.0)
        ais = breakdown.get("aisTrajectoryMatchScore", 0.0)
        environmental = breakdown.get("environmentalDriftScore", 0.0)

        # Weighted formula: Confidence = 0.35*SAR + 0.25*Optical + 0.25*AIS + 0.15*Environmental
        expected_confidence = round(0.35 * sar + 0.25 * optical + 0.25 * ais + 0.15 * environmental, 1)
        formula_matched = (confidence is not None and abs(confidence - expected_confidence) < 0.05)
        passed = (status_aoi == 200 and formula_matched)

        results.append(log_qa(
            "Test 7: Multi-Factor Confidence Score Calculation",
            passed,
            f"Formula: 0.35*{sar} + 0.25*{optical} + 0.25*{ais} + 0.15*{environmental} = {expected_confidence}%, Received: {confidence}%"
        ))
    except Exception as e:
        results.append(log_qa("Test 7: Multi-Factor Confidence Score Calculation", False, str(e)))

    # ------------------------------------------------------------------
    # Summary Evaluation
    # ------------------------------------------------------------------
    total = len(results)
    passed_count = sum(1 for r in results if r)
    print("==================================================================")
    print(f"       QA SUMMARY: {passed_count} / {total} VERIFICATION TESTS PASSED       ")
    print("==================================================================")

    return passed_count == total

if __name__ == '__main__':
    success = run_qa_suite()
    sys.exit(0 if success else 1)
