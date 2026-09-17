"""Stage 4: confirm / review / reject using SAR + optical + metocean."""
from __future__ import annotations


def fuse(sar: dict, optical: dict, metocean: dict, ais: dict | None = None) -> dict:
    sar_score = float(sar.get("sarScore") or 0)
    opt_status = optical.get("status")
    opt_score = optical.get("score")
    wind = metocean.get("window") or "unknown"
    env_score = float(metocean.get("score") or 50)
    ais_score = float((ais or {}).get("score") or 28)

    optical_usable = opt_status in ("confirmed", "weak", "rejected") and opt_score is not None

    if optical_usable:
        confidence = 0.40 * sar_score + 0.25 * float(opt_score) + 0.20 * env_score + 0.15 * ais_score
    else:
        # Cloudy / missing S2: do not pretend optical confirmed the slick.
        confidence = 0.50 * sar_score + 0.30 * env_score + 0.20 * ais_score
        confidence = min(confidence, 82.0)

    reasons = []
    status = "review"

    if wind == "fail":
        status = "rejected"
        reasons.append("metocean wind window failed (SAR look-alike regime)")
    elif opt_status == "rejected" and sar_score < 70:
        status = "rejected"
        reasons.append("Sentinel-2 does not support the SAR candidate")
    elif opt_status == "rejected" and wind == "valid" and sar_score >= 70:
        status = "review"
        reasons.append("Strong SAR but optical disagrees — keep for analyst review")
    elif sar_score >= 55 and wind == "valid" and opt_status in ("confirmed", "cloudy", "no_scene", "inconclusive", "weak"):
        if opt_status == "confirmed" or (opt_status in ("cloudy", "no_scene", "inconclusive") and sar_score >= 62):
            status = "confirmed"
            reasons.append("SAR dark-slick + valid wind" + (" + optical darkening" if opt_status == "confirmed" else " (optical unavailable)"))
        else:
            status = "review"
            reasons.append("SAR candidate with valid wind; optical only weakly supportive")
    elif sar_score >= 45 and wind in ("valid", "marginal", "unknown"):
        status = "review"
        reasons.append("Borderline SAR / metocean — not auto-confirmed")
    else:
        status = "rejected"
        reasons.append("Insufficient SAR evidence after metocean/optical gates")

    if status == "confirmed" and confidence < 58:
        status = "review"
        reasons.append("Composite confidence below confirm threshold")

    severity = "LOW"
    if status == "confirmed" and confidence >= 75:
        severity = "HIGH"
    elif status in ("confirmed", "review"):
        severity = "REVIEW" if status == "review" or confidence < 75 else "HIGH"

    return {
        "status": status,
        "severity": severity,
        "confidence": round(float(confidence), 1),
        "reasons": reasons,
        "weights": {
            "sar": 0.40 if optical_usable else 0.50,
            "optical": 0.25 if optical_usable else 0.0,
            "metocean": 0.20 if optical_usable else 0.30,
            "ais": 0.15 if optical_usable else 0.20,
        },
    }
