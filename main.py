from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
import os, time
import psycopg2
from psycopg2.extras import RealDictCursor, Json

from execution.adapter import resolve_execution_mode, session_allowed
from execution.tradestation import submit_paper_order
from ai.analyzer import analyze_ledger

app = FastAPI(title="Ω PRIME Core")

DATABASE_URL = os.getenv("DATABASE_URL")
ENV_KILL_SWITCH_DEFAULT = os.getenv("KILL_SWITCH", "false").lower() == "true"
WEBHOOK_KEY = os.getenv("WEBHOOK_KEY")
ENV_MODE_DEFAULT = os.getenv("MARKET_MODE", "EQUITY").upper()

# --------------------
# DATABASE
# --------------------
def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# --------------------
# SETTINGS
# --------------------
def get_setting(key: str, default: str) -> str:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS omega_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.commit()
    cur.execute("SELECT value FROM omega_settings WHERE key = %s;", (key,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["value"] if row else default

def set_setting(key: str, value: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO omega_settings (key, value)
        VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
    """, (key, value))
    conn.commit()
    cur.close()
    conn.close()

def effective_kill_switch() -> bool:
    v = get_setting("kill_switch", "true" if ENV_KILL_SWITCH_DEFAULT else "false")
    return v.lower() == "true"

def effective_market_mode() -> str:
    v = get_setting("market_mode", ENV_MODE_DEFAULT).upper()
    return v if v in {"EQUITY", "CRYPTO"} else "EQUITY"

# --------------------
# USERS
# --------------------
def parse_kv_env(v):
    if not v: return {}
    out = {}
    for pair in v.split(","):
        if "=" in pair:
            k, val = pair.split("=", 1)
            out[k.strip().upper()] = val.strip()
    return out

USER_ROLES = parse_kv_env(os.getenv("OMEGA_USERS", ""))
USER_TOKENS = parse_kv_env(os.getenv("OMEGA_USER_TOKENS", ""))

def resolve_identity(uid, token):
    if not uid: return ("ANON", "READ")
    uid = uid.upper()
    if USER_TOKENS.get(uid) != token:
        return (uid, "READ")
    return (uid, USER_ROLES.get(uid, "READ"))

# --------------------
# MODELS
# --------------------
class OmegaPayload(BaseModel):
    price: float | None = None
    session: str | None = None

class DecisionIn(BaseModel):
    symbol: str
    timeframe: str
    decision: str
    stance: str
    confidence: int
    tier: str
    payload: OmegaPayload

# --------------------
# EXECUTION OBSERVABILITY
# --------------------
def record_execution_event(decision_id, status, latency_ms=None, error=None):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS execution_events (
            id SERIAL PRIMARY KEY,
            decision_id INTEGER,
            status TEXT NOT NULL,
            latency_ms INTEGER,
            error TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    conn.commit()

    cur.execute("""
        INSERT INTO execution_events (decision_id, status, latency_ms, error)
        VALUES (%s, %s, %s, %s);
    """, (decision_id, status, latency_ms, error))
    conn.commit()
    cur.close()
    conn.close()

@app.get("/execution/events")
def get_execution_events(limit: int = 50):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM execution_events
        ORDER BY created_at DESC
        LIMIT %s;
    """, (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

# --------------------
# LEDGER
# --------------------
@app.post("/ledger/decision")
def record_decision(
    d: DecisionIn,
    x_user_id: str | None = Header(default=None),
    x_user_token: str | None = Header(default=None),
    x_webhook_key: str | None = Header(default=None),
):
    if x_webhook_key:
        if x_webhook_key != WEBHOOK_KEY:
            raise HTTPException(403, "Invalid webhook key")
        uid, role = ("TRADINGVIEW", "ADMIN")
    else:
        uid, role = resolve_identity(x_user_id, x_user_token)

    if role != "ADMIN":
        raise HTTPException(403, "ADMIN required")

    start = time.time()

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS decision_ledger (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            symbol TEXT,
            timeframe TEXT,
            decision TEXT,
            stance TEXT,
            confidence INTEGER,
            tier TEXT,
            payload JSONB
        );
    """)
    conn.commit()

    cur.execute("""
        INSERT INTO decision_ledger
        (symbol, timeframe, decision, stance, confidence, tier, payload)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        RETURNING id;
    """, (
        d.symbol, d.timeframe, d.decision,
        d.stance, d.confidence, d.tier,
        Json(d.payload.dict())
    ))
    decision_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()

    exec_mode = resolve_execution_mode(d.payload.dict())

    if effective_kill_switch():
        record_execution_event(decision_id, "BLOCKED_KILL_SWITCH")
        return {"status": "blocked", "decision_id": decision_id}

    try:
        if exec_mode == "PAPER" and d.stance == "ENTER":
            submit_paper_order(d.dict())
            latency = int((time.time() - start) * 1000)
            record_execution_event(decision_id, "SUBMITTED", latency)
    except Exception as e:
        record_execution_event(decision_id, "ERROR", error=str(e))
        raise

    return {"status": "recorded", "decision_id": decision_id}

@app.get("/ledger/decisions")
def get_decisions():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM decision_ledger ORDER BY created_at DESC;")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"decisions": rows}

@app.get("/ai/insights")
def ai_insights():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM decision_ledger;")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return analyze_ledger(rows)
