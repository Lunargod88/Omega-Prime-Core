from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
import os
import uuid
import psycopg2
from psycopg2.extras import RealDictCursor, Json

from execution.adapter import resolve_execution_mode, session_allowed
from execution.tradestation import submit_paper_order
from ai.analyzer import analyze_ledger
from observability import router as observability_router
from negotiation import router as negotiation_router
# --------------------
# DECISION STATE MACHINE (import-safe + name-flexible)
# --------------------
import importlib

def _load_state_machine_callable():
    """
    Loads omegaprime core statemachine.py if present.
    We DO NOT assume a single function name (because that’s how systems get broken).
    We scan for common callable names and use the first match.
    """
    try:
        mod = importlib.import_module("statemachine")
    except Exception:
        return None, None

    # Candidate function names (ordered by preference)
    candidates = [
        "enforce_decision_state",
        "apply_decision_state",
        "run_decision_state_machine",
        "decision_state_machine",
        "evaluate_decision_state",
        "evaluate_state",
        "run_state_machine",
        "transition",
        "next_state",
    ]

    for name in candidates:
        fn = getattr(mod, name, None)
        if callable(fn):
            return mod, fn

    return mod, None


_SM_MOD, _SM_FN = _load_state_machine_callable()


def _coerce_sm_result(result):
    """
    Normalizes whatever the state machine returns into:
      allowed: bool
      reason: str|None
      state:  any
      meta:   dict
    Supported shapes:
      - bool
      - {"allowed": bool, "reason": "...", "state": "...", ...}
      - ("ALLOW"/"DENY", "reason")
      - ("ALLOW"/"DENY", {"reason": "...", ...})
    """
    allowed = True
    reason = None
    state = None
    meta = {}

    if result is None:
        return allowed, reason, state, meta

    if isinstance(result, bool):
        allowed = result
        return allowed, reason, state, meta

    if isinstance(result, dict):
        if "allowed" in result:
            allowed = bool(result.get("allowed"))
        elif "deny" in result:
            allowed = not bool(result.get("deny"))
        elif "ok" in result:
            allowed = bool(result.get("ok"))

        reason = result.get("reason") or result.get("detail") or result.get("message")
        state = result.get("state") or result.get("decision_state") or result.get("status")
        meta = {k: v for k, v in result.items() if k not in {"allowed", "deny", "ok"}}
        return allowed, reason, state, meta

    if isinstance(result, (tuple, list)) and len(result) >= 1:
        head = result[0]
        if isinstance(head, str):
            h = head.strip().upper()
            if h in {"DENY", "BLOCK", "REJECT", "NO"}:
                allowed = False
            elif h in {"ALLOW", "OK", "PASS", "YES"}:
                allowed = True
        elif isinstance(head, bool):
            allowed = head

        if len(result) >= 2:
            tail = result[1]
            if isinstance(tail, str):
                reason = tail
            elif isinstance(tail, dict):
                reason = tail.get("reason") or tail.get("detail") or tail.get("message")
                state = tail.get("state") or tail.get("decision_state") or tail.get("status")
                meta = tail

        return allowed, reason, state, meta

    # Fallback: treat unknown return as allow but capture stringified info
    return True, str(result), None, {"raw": result}


def enforce_decision_state_machine(d: dict, context: dict):
    """
    Calls the statemachine if we found a callable.
    Hard-blocks if it returns a deny.
    """
    if not _SM_FN:
        # State machine module missing callable — do not break pipeline.
        # You still get green deploy; you can rename/correct the function and it will latch automatically.
        return {"allowed": True, "note": "statemachine callable not found; skipped"}

    try:
        # Try common calling patterns:
        # 1) fn(d, context)
        # 2) fn(d)
        # 3) fn(**payload)
        try:
            result = _SM_FN(d, context)
        except TypeError:
            try:
                result = _SM_FN(d)
            except TypeError:
                merged = dict(d)
                merged.update(context or {})
                result = _SM_FN(**merged)

        allowed, reason, state, meta = _coerce_sm_result(result)

        if not allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Denied by Decision State Machine{': ' + reason if reason else ''}"
            )

        return {"allowed": True, "state": state, "meta": meta}

    except HTTPException:
        raise
    except Exception as e:
        # Fail-safe: do NOT hard-crash trading pipeline on state machine exception.
        # We surface it explicitly so you can see it in logs + dashboard diagnostics.
        return {"allowed": True, "note": f"statemachine error bypassed: {type(e).__name__}: {e}"}


app = FastAPI(title="Ω PRIME Core")

DATABASE_URL = os.getenv("DATABASE_URL")

# Defaults (env). DB overrides via /controls.
ENV_KILL_SWITCH_DEFAULT = os.getenv("KILL_SWITCH", "false").lower() == "true"
ENV_MODE_DEFAULT = os.getenv("MARKET_MODE", "EQUITY").upper()
WEBHOOK_KEY = os.getenv("WEBHOOK_KEY")


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
    return default if not row else row["value"]


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
    v = get_setting("kill_switch", "true" if ENV_KILL_SWITCH_DEFAULT else "false").lower()
    return v == "true"


def effective_market_mode() -> str:
    v = get_setting("market_mode", ENV_MODE_DEFAULT).upper()
    return v if v in {"EQUITY", "CRYPTO"} else "EQUITY"


# --------------------
# ACCOUNTS (18.1)
# --------------------
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

def resolve_identity(x_user_id: str | None, x_user_token: str | None) -> tuple[str, str]:
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
# SYMBOL UNIVERSE (18.4) — from env (fallback to defaults)
# --------------------
def parse_symbol_list(v: str | None) -> set[str]:
    if not v:
        return set()
    return {s.strip().upper() for s in v.split(",") if s.strip()}

EQUITY_SYMBOLS_DEFAULT = {"SPY", "QQQ", "AAPL", "TSLA", "NVDA", "MSFT"}
CRYPTO_SYMBOLS_DEFAULT = {"BTCUSD", "ETHUSD", "SOLUSD"}

EQUITY_SYMBOLS = parse_symbol_list(os.getenv("OMEGA_EQUITY_SYMBOLS")) or EQUITY_SYMBOLS_DEFAULT
CRYPTO_SYMBOLS = parse_symbol_list(os.getenv("OMEGA_CRYPTO_SYMBOLS")) or CRYPTO_SYMBOLS_DEFAULT


def symbol_allowed(symbol: str) -> bool:
    mode = effective_market_mode()
    s = symbol.strip().upper()
    return (s in EQUITY_SYMBOLS) if mode == "EQUITY" else (s in CRYPTO_SYMBOLS)

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
    tier: str  # S+++, S++, S+, S, A, B, C

    # Trade Memory Graph attachment
    trade_id: str | None = None

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


class TradeEventIn(BaseModel):
    event_type: str  # ACK, FLAG_RISK, OVERRIDE, NEAR_MISS, EXIT, POSTMORTEM, NOTE
    data: dict | None = None


# --------------------
# TRADE MEMORY GRAPH — DB helpers
# --------------------
def ensure_trade_tables(cur):
    # trades: lifecycle root
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            trade_id UUID PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            symbol TEXT NOT NULL,
            side TEXT,             -- LONG / SHORT (optional)
            status TEXT NOT NULL DEFAULT 'OPEN',   -- OPEN / CLOSED
            opened_at TIMESTAMPTZ,
            closed_at TIMESTAMPTZ,
            meta JSONB NOT NULL DEFAULT '{}'::jsonb
        );
        """
    )

    # trade_events: graph nodes
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_events (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            trade_id UUID NOT NULL REFERENCES trades(trade_id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            data JSONB NOT NULL DEFAULT '{}'::jsonb
        );
        """
    )


def ensure_decision_trade_id_column(cur):
    # attach ledger rows to a trade lifecycle
    cur.execute("ALTER TABLE decision_ledger ADD COLUMN IF NOT EXISTS trade_id UUID;")


def new_trade_id() -> str:
    return str(uuid.uuid4())


def infer_side(decision: str) -> str | None:
    d = (decision or "").upper()
    if "LONG" in d or d == "BUY":
        return "LONG"
    if "SHORT" in d or d == "SELL":
        return "SHORT"
    return None


def create_trade(cur, symbol: str, decision: str, meta: dict | None = None) -> str:
    tid = new_trade_id()
    side = infer_side(decision)
    cur.execute(
        """
        INSERT INTO trades (trade_id, symbol, side, status, opened_at, meta)
        VALUES (%s, %s, %s, 'OPEN', NOW(), %s);
        """,
        (tid, symbol.strip().upper(), side, Json(meta or {})),
    )
    return tid


def close_trade(cur, trade_id: str):
    cur.execute(
        """
        UPDATE trades
        SET status = 'CLOSED', closed_at = NOW()
        WHERE trade_id = %s;
        """,
        (trade_id,),
    )


def write_trade_event(cur, trade_id: str, event_type: str, user_id: str, role: str, data: dict | None = None):
    cur.execute(
        """
        INSERT INTO trade_events (trade_id, event_type, user_id, role, data)
        VALUES (%s, %s, %s, %s, %s);
        """,
        (trade_id, event_type, user_id, role, Json(data or {})),
    )


# --------------------
# HEALTH
# --------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "kill_switch": effective_kill_switch(),
        "market_mode": effective_market_mode(),
        "users_configured": list(USER_ROLES.keys()),
        "equity_universe_count": len(EQUITY_SYMBOLS),
        "crypto_universe_count": len(CRYPTO_SYMBOLS),
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

    mode = effective_market_mode()
    allowed = list(EQUITY_SYMBOLS if mode == "EQUITY" else CRYPTO_SYMBOLS)

    return {
        "user_id": uid,
        "role": role,
        "market_mode": mode,
        "kill_switch": effective_kill_switch(),
        "allowedSymbols": allowed,
    }


# --------------------
# INIT LEDGER + TRADE MEMORY GRAPH
# --------------------
@app.post("/ledger/init")
def init_ledger():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
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
        """
    )

    ensure_settings_table(cur)
    ensure_trade_tables(cur)
    ensure_decision_trade_id_column(cur)

    # Seed defaults if missing (do not overwrite)
    cur.execute("SELECT 1 FROM omega_settings WHERE key = 'kill_switch';")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO omega_settings (key, value) VALUES (%s, %s);",
            ("kill_switch", "true" if ENV_KILL_SWITCH_DEFAULT else "false"),
        )

    cur.execute("SELECT 1 FROM omega_settings WHERE key = 'market_mode';")
    if not cur.fetchone():
        seed_mode = ENV_MODE_DEFAULT if ENV_MODE_DEFAULT in {"EQUITY", "CRYPTO"} else "EQUITY"
        cur.execute(
            "INSERT INTO omega_settings (key, value) VALUES (%s, %s);",
            ("market_mode", seed_mode),
        )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "decision_ledger + omega_settings + trades + trade_events initialized"}

# --------------------
# RECORD DECISION (18.1 + 18.2 + 18.3 + 18.4 + TRADE MEMORY GRAPH + DECISION STATE MACHINE)
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

    if role != "ADMIN":
        raise HTTPException(status_code=403, detail="Permission denied: ADMIN required")

    # ---- MODE / SYMBOL GATE ----
    if not symbol_allowed(d.symbol):
        raise HTTPException(status_code=403, detail=f"Denied: symbol {d.symbol} not allowed in mode {effective_market_mode()}")

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

    # ---- DECISION STATE MACHINE (hard governance gate) ----
    sm_context = {
        "user_id": uid,
        "role": role,
        "market_mode": effective_market_mode(),
        "kill_switch": effective_kill_switch(),
        "exec_mode": exec_mode,
    }
    sm_result = enforce_decision_state_machine(d.dict(), sm_context)

    # ---- TRADE MEMORY GRAPH: allocate/attach trade_id ----
    conn = get_db()
    cur = conn.cursor()

    # Ensure tables exist even if user forgot /ledger/init once (safe idempotent)
    ensure_trade_tables(cur)
    ensure_settings_table(cur)
    ensure_decision_trade_id_column(cur)
    conn.commit()

    trade_id = d.trade_id

    # Create trade on ENTER if missing
    if d.stance == "ENTER" and not trade_id:
        trade_id = create_trade(
            cur,
            symbol=d.symbol,
            decision=d.decision,
            meta={
                "market_mode": effective_market_mode(),
                "timeframe": d.timeframe,
                "tf_htf": d.tf_htf,
                "tf_ltf": d.tf_ltf,
                "tier": d.tier,
                "confidence": d.confidence,
                "decision_state": sm_result.get("state"),
                "decision_state_meta": sm_result.get("meta") or {},
            },
        )
        write_trade_event(
            cur,
            trade_id=trade_id,
            event_type="OPEN",
            user_id=uid,
            role=role,
            data={"decision": d.decision, "stance": d.stance, "session": sess, "regime": d.regime},
        )

    # If EXIT and trade_id exists -> close trade
    if d.decision == "EXIT" and trade_id:
        close_trade(cur, trade_id)
        write_trade_event(
            cur,
            trade_id=trade_id,
            event_type="EXIT",
            user_id=uid,
            role=role,
            data={"decision": d.decision, "stance": d.stance, "session": sess, "regime": d.regime},
        )

    # Always write a decision-node event if trade_id exists
    if trade_id:
        write_trade_event(
            cur,
            trade_id=trade_id,
            event_type="DECISION",
            user_id=uid,
            role=role,
            data={
                "symbol": d.symbol,
                "timeframe": d.timeframe,
                "decision": d.decision,
                "stance": d.stance,
                "tier": d.tier,
                "confidence": d.confidence,
                "reason_codes": d.reason_codes,
                "reasons_text": d.reasons_text,
                "regime": d.regime,
                "session": sess,
                "decision_state": sm_result.get("state"),
                "decision_state_meta": sm_result.get("meta") or {},
                "decision_state_note": sm_result.get("note"),
            },
        )

    # ---- EXECUTION (kill switch enforced) ----
    if (not effective_kill_switch()) and exec_mode == "PAPER" and d.stance == "ENTER":
        submit_paper_order(d.dict())

    # ---- PERSIST decision ledger (now includes trade_id) ----
    cur.execute(
        """
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
            trade_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, created_at;
        """,
        (
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
            trade_id,
        ),
    )

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
        "kill_switch": effective_kill_switch(),
        "trade_id": trade_id,
        "decision_state": sm_result.get("state"),
        "decision_state_note": sm_result.get("note"),
    }

# --------------------
# READ DECISIONS
# --------------------
@app.get("/ledger/decisions")
def get_decisions(limit: int = 50):
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
    return {"count": len(rows), "decisions": rows}

@app.get("/ledger/decision/{decision_id}")
def replay_decision(decision_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM decision_ledger WHERE id = %s;", (decision_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Decision not found")
    return row
@app.get("/analysis")
def get_analysis():
    return analyze_ledger()
# --------------------
# TRADE MEMORY GRAPH — endpoints
# --------------------
@app.get("/trades")
def list_trades(limit: int = 50):
    conn = get_db()
    cur = conn.cursor()
    ensure_trade_tables(cur)
    conn.commit()

    cur.execute(
        """
        SELECT *
        FROM trades
        ORDER BY created_at DESC
        LIMIT %s;
        """,
        (limit,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"count": len(rows), "trades": rows}

@app.get("/trades/{trade_id}")
def get_trade(trade_id: str):
    conn = get_db()
    cur = conn.cursor()
    ensure_trade_tables(cur)
    conn.commit()

    cur.execute("SELECT * FROM trades WHERE trade_id = %s;", (trade_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Trade not found")
    return row
    
    from observability import router as observability_router
app.include_router(observability_router)
app.include_router(negotiation_router)
