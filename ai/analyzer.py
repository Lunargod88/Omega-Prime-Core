import psycopg2
from psycopg2.extras import RealDictCursor
import os
from statistics import mean

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def analyze_ledger(limit: int = 50):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM decision_ledger
        ORDER BY created_at DESC
        LIMIT %s;
        """,
        (limit,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        return {
            "status": "no_data",
            "summary": "No decisions available for analysis"
        }

    confidences = [r["confidence"] for r in rows if r.get("confidence") is not None]
    tiers = [r["tier"] for r in rows if r.get("tier")]
    stances = [r["stance"] for r in rows if r.get("stance")]

    avg_conf = round(mean(confidences), 2) if confidences else 0

    contradiction_count = sum(
        1 for r in rows if r["decision"] == "BUY" and r["stance"] == "DENIED"
    )

    high_conf_low_tier = [
        r for r in rows if r["confidence"] >= 80 and r["tier"] in {"B", "C"}
    ]

    return {
        "status": "analyzed",
        "rows_analyzed": len(rows),
        "avg_confidence": avg_conf,
        "tiers_seen": list(set(tiers)),
        "stances_seen": list(set(stances)),
        "contradictions": contradiction_count,
        "high_conf_low_tier": len(high_conf_low_tier),
        "verdict": verdict_engine(avg_conf, contradiction_count, len(high_conf_low_tier))
    }

def verdict_engine(avg_conf, contradictions, risky_signals):
    if contradictions > 5:
        return "SYSTEM CONFLICT DETECTED"
    if risky_signals > 3:
        return "QUALITY CONTROL ALERT"
    if avg_conf >= 80:
        return "SYSTEM OPERATING WITH HIGH CONFIDENCE"
    if avg_conf >= 60:
        return "SYSTEM OPERATING WITH MODERATE CONFIDENCE"
    return "SYSTEM CONFIDENCE DEGRADED"
