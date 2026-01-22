from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
import os
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from datetime import datetime, timezone

from execution.adapter import resolve_execution_mode, session_allowed
from execution.tradestation import submit_paper_order
from ai.analyzer import analyze_ledger

app = FastAPI(title="Ω PRIME Core")

DATABASE_URL = os.getenv("DATABASE_URL")

# If env is set, it acts as DEFAULT. We also support DB override via /controls.
ENV_KILL_SWITCH_DEFAULT = os.getenv("KILL_SWITCH", "false").lower() == "true"
WEBHOOK_KEY = os.getenv("WEBHOOK_KEY")
ENV_MODE_DEFAULT = os.getenv("MARKET_MODE", "EQUITY").upper()

# --------------------
# DATABASE
# --------------------
def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def ensure_settings_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS omega_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )

def get_setting(key: str, default: str) -> str:
    conn = get_db()
    cur = conn.cursor()
    ensure_settings_table(cur)
    conn.commit()

    cur.execute("SELECT value FROM omega_settings WHERE key = %s;", (key,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return default
    return row["value"]

def set_setting(key: str, value: str) -> None:
    conn = get_db()
    cur = conn.cursor()
    ensure_settings_table(cur)
    conn.commit()

    cur.execute(
        """
        INSERT INTO omega_settings (key, value)
        VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
        """,
        (key, value),
    )
    conn.commit()
    cur.close()
    conn.close()

def effective_kill_switch() -> bool:
    # DB override wins; otherwise env default.
    v = get_setting("kill_switch", "true" if ENV_KILL_SWITCH_DEFAULT else "false").lower()
    return v == "true"

def effective_market_mode() -> str:
    v = get_setting("market_mode", ENV_MODE_DEFAULT).upper()
    return v if v in {"EQUITY", "CRYPTO"} else "EQUITY"

# --------------------
# ACCOUNTS (STEP 18.1)
# --------------------
# Env format:
# OMEGA_USERS="JAYLYN=ADMIN,WIFE=CONFIRM"
# OMEGA_USER_TOKENS="JAYLYN=token1,WIFE=token2"
def parse_kv_env(env_value: str | None) -> dict[str, str]:
    if not env_value:
        return {}
    items = [x.strip() for x in env_value.split(",") if x.strip()]
    out: dict[str, str] = {}
    for it in items:
        if "=" not in it:
            continue
        k, v = it.split("=", 1)
        out[k.strip().upper()] = v.strip()
    return out

USER_ROLES = parse_kv_env(os.getenv("OMEGA_USERS", ""))
USER_TOKENS = parse_kv_env(os.getenv("OMEGA_USER_TOKENS", ""))

def resolve_identity(
    x_user_id: str | None,
    x_user_token: str | None
) -> tuple[str, str]:
    """
    Returns (user_id, role).
    If missing/invalid token -> READ.
    """
    if not x_user_id:
        return ("ANON", "READ")

    uid = x_user_id.strip().upper()
    expected_token = USER_TOKENS.get(uid)

    if not expected_token:
        return (uid, "READ")

    if not x_user_token or x_user_token.strip() != expected_token:
        return (uid, "READ")

    role = USER_ROLES.get(uid, "READ").upper()
    if role not in {"READ", "CONFIRM", "ADMIN"}:
        role = "READ"
    return (uid, role)

# --------------------
# MODELS
# --------------------
class OmegaPayload(BaseModel):
    price: float | None = None
    chiTier: str | None = None
    omegaConf: int | None = None
    memNet: int | None = None
    whaleIntentScore: int | None = None
    expectedRRLow: float | None = None
    expectedRRHigh: float | None = None
    rrStopPrice: float | None = None
    execRegime: str | None = None
    execStance: str | None = None
    session: str | None = None

class DecisionIn(BaseModel):
    symbol: str
    timeframe: str
    decision: str
    stance: str  # ENTER / HOLD / STAND_DOWN / DENIED
    confidence: int
    tier: str

    reason_codes: list[str] | None = None
    reasons_text: list[str] | None = None

    regime: str | None = None
    session: str | None = None
    tf_htf: str | None = None
    tf_ltf: str | None = None

    payload: OmegaPayload

class ControlToggle(BaseModel):
    enabled: bool

class ModeToggle(BaseModel):
    mode: str  # EQUITY or CRYPTO

# --------------------
# SYMBOL UNIVERSES (ENV-DRIVEN)
# --------------------
def parse_symbol_list(env_value: str | None, fallback: set[str]) -> set[str]:
    if not env_value:
        return set(fallback)
    items = [x.strip().upper() for x in env_value.split(",") if x.strip()]
    return set(items) if items else set(fallback)

DEFAULT_EQUITY_SYMBOLS = {"SPY", "QQQ", "AAPL", "TSLA", "NVDA", "MSFT"}
DEFAULT_CRYPTO_SYMBOLS = {"BTCUSD", "ETHUSD", "SOLUSD"}

EQUITY_SYMBOLS = parse_symbol_list(os.getenv("OMEGA_EQUITY_SYMBOLS"), DEFAULT_EQUITY_SYMBOLS)
CRYPTO_SYMBOLS = parse_symbol_list(os.getenv("OMEGA_CRYPTO_SYMBOLS"), DEFAULT_CRYPTO_SYMBOLS)

def symbol_allowed(symbol: str) -> bool:
    mode = effective_market_mode()
    s = symbol.strip().upper()
    if mode == "EQUITY":
        return s in EQUITY_SYMBOLS
    return s in CRYPTO_SYMBOLS

# --------------------
# DECISION STATE MACHINE (STEP 19.X — CORE BRAIN)
# --------------------
STATES = {
    "STAND_DOWN",
    "SCOUTING",
    "ACCUMULATING",
    "EXPANDING",
    "DEFENDING",
    "EXITING",
}

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def compute_next_state(prev_state: str | None, d: DecisionIn) -> tuple[str, str]:
    """
    Returns (next_state, transition_reason).
    Deterministic, conservative v1.
    """
    prev = (prev_state or "STAND_DOWN").upper()
    if prev not in STATES:
        prev = "STAND_DOWN"

    stance = (d.stance or "").upper()
    decision = (d.decision or "").upper()

    # Hard stops
    if stance in {"DENIED", "STAND_DOWN"}:
        return ("STAND_DOWN", f"{stance} received")

    if decision == "EXIT":
        return ("EXITING", "EXIT decision received")

    # Entries
    if stance == "ENTER":
        # v1: immediate accumulation state
        return ("ACCUMULATING", "ENTER stance received")

    # Holds
    if stance == "HOLD":
        if prev in {"ACCUMULATING", "EXPANDING", "DEFENDING"}:
            return (prev, "HOLD maintains current in-trade state")
        # If we're holding but we had no known in-trade state, default safe
        return ("DEFENDING", "HOLD without prior in-trade state -> DEFENDING")

    # Default
    return ("STAND_DOWN", "Defaulted to STAND_DOWN")

def ensure_state_tables(cur):
    # Current state per symbol
    cur.execute("""
        CREATE TABLE IF NOT EXISTS omega_symbol_state (
            symbol TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_decision_id INTEGER,
            last_transition_reason TEXT
        );
    """)

    # State transition audit log
    cur.execute("""
        CREATE TABLE IF NOT EXISTS omega_state_transitions (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            symbol TEXT NOT NULL,
            prev_state TEXT NOT NULL,
            next_state TEXT NOT NULL,
            reason TEXT NOT NULL,
            decision_id INTEGER
        );
    """)

def get_symbol_state(symbol: str) -> str:
    s = symbol.strip().upper()
    conn = get_db()
    cur = conn.cursor()
    ensure_state_tables(cur)
    conn.commit()

    cur.execute("SELECT state FROM omega_symbol_state WHERE symbol=%s;", (s,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return "STAND_DOWN"
    return (row["state"] or "STAND_DOWN").upper()

def upsert_symbol_state(symbol: str, state: str, decision_id: int | None, reason: str):
    s = symbol.strip().upper()
    st = state.upper()
    if st not in STATES:
        st = "STAND_DOWN"

    conn = get_db()
    cur = conn.cursor()
    ensure_state_tables(cur)
    conn.commit()

    cur.execute("""
        INSERT INTO omega_symbol_state (symbol, state, updated_at, last_decision_id, last_transition_reason)
        VALUES (%s, %s, NOW(), %s, %s)
        ON CONFLICT (symbol) DO UPDATE
        SET state = EXCLUDED.state,
            updated_at = NOW(),
            last_decision_id = EXCLUDED.last_decision_id,
            last_transition_reason = EXCLUDED.last_transition_reason;
    """, (s, st, decision_id, reason))

    conn.commit()
    cur.close()
    conn.close()

def record_transition(symbol: str, prev_state: str, next_state: str, reason: str, decision_id: int | None):
    s = symbol.strip().upper()
    conn = get_db()
    cur = conn.cursor()
    ensure_state_tables(cur)
    conn.commit()

    cur.execute("""
        INSERT INTO omega_state_transitions (symbol, prev_state, next_state, reason, decision_id)
        VALUES (%s, %s, %s, %s, %s);
    """, (s, prev_state, next_state, reason, decision_id))

    conn.commit()
    cur.close()
    conn.close()

# --------------------
# HEALTH
# --------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "kill_switch": effective_kill_switch(),
        "market_mode": effective_market_mode(),
        "users_configured": list(USER_ROLES.keys())
    }

# --------------------
# CONTROLS (18.3 + 18.4)
# --------------------
@app.get("/controls")
def get_controls():
    return {
        "kill_switch": effective_kill_switch(),
        "market_mode": effective_market_mode(),
        "equity_symbols": sorted(list(EQUITY_SYMBOLS)),
        "crypto_symbols": sorted(list(CRYPTO_SYMBOLS)),
    }

@app.post("/controls/kill-switch")
def set_kill_switch(
    body: ControlToggle,
    x_user_id: str | None = Header(default=None),
    x_user_token: str | None = Header(default=None),
):
    uid, role = resolve_identity(x_user_id, x_user_token)
    if role != "ADMIN":
        raise HTTPException(status_code=403, detail="ADMIN required")

    set_setting("kill_switch", "true" if body.enabled else "false")
    return {"status": "ok", "kill_switch": effective_kill_switch(), "by": uid}

@app.post("/controls/mode")
def set_mode(
    body: ModeToggle,
    x_user_id: str | None = Header(default=None),
    x_user_token: str | None = Header(default=None),
):
    uid, role = resolve_identity(x_user_id, x_user_token)
    if role != "ADMIN":
        raise HTTPException(status_code=403, detail="ADMIN required")

    mode = body.mode.strip().upper()
    if mode not in {"EQUITY", "CRYPTO"}:
        raise HTTPException(status_code=400, detail="mode must be EQUITY or CRYPTO")

    set_setting("market_mode", mode)
    return {"status": "ok", "market_mode": effective_market_mode(), "by": uid}

@app.get("/me")
def me(
    x_user_id: str | None = Header(default=None),
    x_user_token: str | None = Header(default=None),
):
    uid, role = resolve_identity(x_user_id, x_user_token)
    return {"user_id": uid, "role": role}

# --------------------
# INIT LEDGER (NOW ALSO ENSURES STATE TABLES + LEDGER COLUMNS)
# --------------------
@app.post("/ledger/init")
def init_ledger():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS decision_ledger (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,

            decision TEXT NOT NULL,
            stance TEXT NOT NULL,

            confidence INTEGER NOT NULL,
            tier TEXT NOT NULL,

            reason_codes TEXT[],
            reasons_text TEXT[],

            regime TEXT,
            session TEXT,
            tf_htf TEXT,
            tf_ltf TEXT,

            payload JSONB NOT NULL
        );
    """)

    ensure_settings_table(cur)
    ensure_state_tables(cur)

    # Add state columns to ledger if missing
    cur.execute("ALTER TABLE decision_ledger ADD COLUMN IF NOT EXISTS state_before TEXT;")
    cur.execute("ALTER TABLE decision_ledger ADD COLUMN IF NOT EXISTS state_after TEXT;")

    # Seed defaults if missing (do not overwrite)
    cur.execute("SELECT 1 FROM omega_settings WHERE key = 'kill_switch';")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO omega_settings (key, value) VALUES (%s, %s);",
            ("kill_switch", "true" if ENV_KILL_SWITCH_DEFAULT else "false"),
        )

    cur.execute("SELECT 1 FROM omega_settings WHERE key = 'market_mode';")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO omega_settings (key, value) VALUES (%s, %s);",
            ("market_mode", "EQUITY" if ENV_MODE_DEFAULT not in {"EQUITY", "CRYPTO"} else ENV_MODE_DEFAULT),
        )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "decision_ledger + omega_settings + state machine initialized"}

# --------------------
# RECORD DECISION (18.x ENFORCED + STATE MACHINE)
# --------------------
@app.post("/ledger/decision")
def record_decision(
    d: DecisionIn,
    x_user_id: str | None = Header(default=None),
    x_user_token: str | None = Header(default=None),
    x_webhook_key: str | None = Header(default=None),
):
    # ---- AUTH ROUTING (TradingView OR Human) ----
    if x_webhook_key is not None:
        if not WEBHOOK_KEY or x_webhook_key.strip() != WEBHOOK_KEY:
            raise HTTPException(status_code=403, detail="Invalid webhook key")
        uid, role = ("TRADINGVIEW", "ADMIN")
    else:
        uid, role = resolve_identity(x_user_id, x_user_token)

    # ---- PERMISSION GATE ----
    if role != "ADMIN":
        raise HTTPException(status_code=403, detail="Permission denied: ADMIN required")

    # ---- MODE / SYMBOL GATE ----
    if not symbol_allowed(d.symbol):
        raise HTTPException(
            status_code=403,
            detail=f"Denied: symbol {d.symbol} not allowed in mode {effective_market_mode()}"
        )

    # ---- VALIDATION ----
    if d.decision not in {"BUY", "SELL", "EXIT", "HOLD", "ENTER LONG", "ENTER SHORT"}:
        raise HTTPException(status_code=400, detail="Invalid decision")

    if d.stance not in {"ENTER", "HOLD", "STAND_DOWN", "DENIED"}:
        raise HTTPException(status_code=400, detail="Invalid stance")

    if not (0 <= d.confidence <= 100):
        raise HTTPException(status_code=400, detail="Confidence out of range")

    # ---- GOVERNOR ----
    if d.confidence < 70:
        raise HTTPException(status_code=403, detail="Denied: confidence gate")

    if d.tier in {"Ø", "S-", "C", "D"}:
        raise HTTPException(status_code=403, detail="Denied: tier gate")

    sess = d.session or d.payload.session
    if sess and sess not in {"RTH", "ETH"}:
        raise HTTPException(status_code=403, detail="Denied: session gate")

    if not session_allowed(sess):
        raise HTTPException(status_code=403, detail="Denied: execution session")

    exec_mode = resolve_execution_mode(d.payload.dict())

    # ---- STATE MACHINE: BEFORE DECISION ----
    prev_state = get_symbol_state(d.symbol)
    next_state, trans_reason = compute_next_state(prev_state, d)

    # ---- EXECUTION (KILL SWITCH) ----
    if (not effective_kill_switch()) and exec_mode == "PAPER" and d.stance == "ENTER":
        submit_paper_order(d.dict())

    # ---- PERSIST ----
    conn = get_db()
    cur = conn.cursor()

    # Ensure new columns exist even if /ledger/init wasn't run after deploy
    cur.execute("ALTER TABLE decision_ledger ADD COLUMN IF NOT EXISTS state_before TEXT;")
    cur.execute("ALTER TABLE decision_ledger ADD COLUMN IF NOT EXISTS state_after TEXT;")
    ensure_state_tables(cur)
    conn.commit()

    cur.execute("""
        INSERT INTO decision_ledger (
            symbol,
            timeframe,
            decision,
            stance,
            confidence,
            tier,
            reason_codes,
            reasons_text,
            regime,
            session,
            tf_htf,
            tf_ltf,
            payload,
            state_before,
            state_after
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, created_at;
    """, (
        d.symbol,
        d.timeframe,
        d.decision,
        d.stance,
        d.confidence,
        d.tier,
        d.reason_codes,
        d.reasons_text,
        d.regime,
        sess,
        d.tf_htf,
        d.tf_ltf,
        Json(d.payload.dict()),
        prev_state,
        next_state
    ))

    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    # ---- STATE MACHINE: AFTER DECISION ----
    decision_id = row["id"]
    record_transition(d.symbol, prev_state, next_state, trans_reason, decision_id)
    upsert_symbol_state(d.symbol, next_state, decision_id, trans_reason)

    return {
        "status": "recorded",
        "id": decision_id,
        "timestamp": row["created_at"].isoformat(),
        "by": uid,
        "role": role,
        "market_mode": effective_market_mode(),
        "kill_switch": effective_kill_switch(),
        "state_before": prev_state,
        "state_after": next_state,
        "transition_reason": trans_reason
    }

# --------------------
# READ DECISIONS (READ SAFE)
# --------------------
@app.get("/ledger/decisions")
def get_decisions(limit: int = 50):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM decision_ledger
        ORDER BY created_at DESC
        LIMIT %s;
    """, (limit,))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return {"count": len(rows), "decisions": rows}

# --------------------
# DECISION REPLAY
# --------------------
@app.get("/ledger/decision/{decision_id}")
def replay_decision(decision_id: int):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM decision_ledger
        WHERE id = %s;
    """, (decision_id,))

    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Decision not found")

    return row

# --------------------
# STATE READOUT (FOR DASHBOARD + OPERATIONS)
# --------------------
@app.get("/state/{symbol}")
def read_state(symbol: str):
    s = symbol.strip().upper()
    if not symbol_allowed(s):
        raise HTTPException(status_code=403, detail="Symbol not allowed in current mode")
    st = get_symbol_state(s)
    return {"symbol": s, "state": st}

@app.get("/state/transitions/{symbol}")
def read_transitions(symbol: str, limit: int = 50):
    s = symbol.strip().upper()
    conn = get_db()
    cur = conn.cursor()
    ensure_state_tables(cur)
    conn.commit()

    cur.execute("""
        SELECT *
        FROM omega_state_transitions
        WHERE symbol = %s
        ORDER BY created_at DESC
        LIMIT %s;
    """, (s, limit))

    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"symbol": s, "count": len(rows), "transitions": rows}

# --------------------
# AI INSIGHTS (READ ONLY)
# --------------------
@app.get("/ai/insights")
def ai_insights():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM decision_ledger
        ORDER BY created_at ASC;
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return analyze_ledger(rows)
