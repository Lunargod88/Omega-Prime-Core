from fastapi import APIRouter
from psycopg2.extras import RealDictCursor
import psycopg2
import os
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL")

router = APIRouter(prefix="/observability", tags=["observability"])

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# --------------------
# SYSTEM SNAPSHOT
# --------------------
@router.get("/system")
def system_snapshot():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT key, value FROM omega_settings;")
    settings = cur.fetchall()

    cur.execute("SELECT COUNT(*) AS decision_count FROM decision_ledger;")
    decision_count = cur.fetchone()["decision_count"]

    cur.execute("SELECT COUNT(*) AS trade_count FROM trades;")
    trade_count = cur.fetchone()["trade_count"]

    cur.close()
    conn.close()

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "settings": settings,
        "decision_count": decision_count,
        "trade_count": trade_count,
    }

# --------------------
# LAST N DECISIONS (RAW)
# --------------------
@router.get("/decisions")
def observe_decisions(limit: int = 50):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM decision_ledger
        ORDER BY created_at DESC
        LIMIT %s;
        """,
        (limit,),
    )

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return {
        "observed_at": datetime.utcnow().isoformat(),
        "count": len(rows),
        "decisions": rows,
    }

# --------------------
# LAST N TRADE EVENTS
# --------------------
@router.get("/trade-events")
def observe_trade_events(limit: int = 100):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM trade_events
        ORDER BY created_at DESC
        LIMIT %s;
        """,
        (limit,),
    )

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return {
        "observed_at": datetime.utcnow().isoformat(),
        "count": len(rows),
        "events": rows,
    }

# --------------------
# FULL TRADE TRACE
# --------------------
@router.get("/trade/{trade_id}")
def observe_trade_lifecycle(trade_id: str):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM trades WHERE trade_id = %s;", (trade_id,))
    trade = cur.fetchone()

    cur.execute(
        "SELECT * FROM trade_events WHERE trade_id = %s ORDER BY created_at ASC;",
        (trade_id,),
    )
    events = cur.fetchall()

    cur.close()
    conn.close()

    return {
        "observed_at": datetime.utcnow().isoformat(),
        "trade": trade,
        "events": events,
    }