from collections import Counter, defaultdict

def analyze_ledger(rows: list[dict]) -> dict:
    if not rows:
        return {"summary": "No data yet"}

    by_regime = defaultdict(list)
    by_session = defaultdict(list)
    stance_counts = Counter()
    tier_counts = Counter()

    for r in rows:
        stance_counts[r["stance"]] += 1
        tier_counts[r["tier"]] += 1

        if r.get("regime"):
            by_regime[r["regime"]].append(r)
        if r.get("session"):
            by_session[r["session"]].append(r)

    regime_stats = {
        k: len(v) for k, v in by_regime.items()
    }

    session_stats = {
        k: len(v) for k, v in by_session.items()
    }

    return {
        "total_decisions": len(rows),
        "stance_distribution": dict(stance_counts),
        "tier_distribution": dict(tier_counts),
        "by_regime": regime_stats,
        "by_session": session_stats,
        "notes": [
            "Read-only analysis",
            "No execution authority",
            "For calibration and review only"
        ]
    }
