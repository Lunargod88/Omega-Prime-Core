from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
import os
import psycopg2
from psycopg2.extras import RealDictCursor, Json

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


def get_setting(key: str, default: str) -> str:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS omega_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
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
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS omega_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
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
# Env format (simple, no JSON):
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
        # user exists nowhere -> READ
        return (uid, "READ")

    if not x_user_token or x_user_token.strip() != expected_token:
        # wrong token -> READ
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
    webhook_key: str | None = None
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
# SYMBOL UNIVERSES (STEP 18.4)
# --------------------
# Keep this tight for now; you can expand later.
EQUITY_SYMBOLS = {"SPY", "QQQ", "AAPL", "TSLA", "NVDA", "MSFT"}
CRYPTO_SYMBOLS = {"BTCUSD", "ETHUSD", "SOLUSD"}


def symbol_allowed(symbol: str) -> bool:
    mode = effective_market_mode()
    s = symbol.strip().upper()
    if mode == "EQUITY":
        return s in EQUITY_SYMBOLS
    return s in CRYPTO_SYMBOLS


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
# INIT LEDGER
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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS omega_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)

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

    return {"status": "decision_ledger + omega_settings initialized"}


# --------------------
# RECORD DECISION (18.1 + 18.2 + 18.3 + 18.4 ENFORCED)
# --------------------
@app.post("/ledger/decision")
def record_decision(
    d: DecisionIn,
    x_user_id: str | None = Header(default=None),
    x_user_token: str | None = Header(default=None),
):
    # ---- AUTH ROUTING (TradingView OR Human) ----

# TradingView webhook path (BODY AUTH)
if d.webhook_key is not None:
    if not WEBHOOK_KEY or d.webhook_key.strip() != WEBHOOK_KEY:
        raise HTTPException(status_code=403, detail="Invalid webhook key")
    uid, role = ("TRADINGVIEW", "ADMIN")

# Human dashboard / API usage
else:
    uid, role = resolve_identity(x_user_id, x_user_token)


    # ---- PERMISSION GATE (18.2) ----
    if role != "ADMIN":
        raise HTTPException(status_code=403, detail="Permission denied: ADMIN required")

    # ---- MODE / SYMBOL GATE (18.4) ----
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

    # ---- EXECUTION (18.3 KILL SWITCH) ----
    if (not effective_kill_switch()) and exec_mode == "PAPER" and d.stance == "ENTER":
        submit_paper_order(d.dict())

    # ---- PERSIST ----
    conn = get_db()
    cur = conn.cursor()

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
            payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
        Json(d.payload.dict())
    ))

    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    return {
        "status": "recorded",
        "id": row["id"],
        "timestamp": row["created_at"].isoformat(),
        "by": uid,
        "role": role,
        "market_mode": effective_market_mode(),
        "kill_switch": effective_kill_switch()
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
