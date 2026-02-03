from fastapi import APIRouter
from db import get_db

router = APIRouter()

@router.get("/symbols")
def get_symbols():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT symbol, market_mode FROM symbol_whitelist;")
    rows = cur.fetchall()

    cur.close()
    conn.close()

    equity = [r["symbol"] for r in rows if r["market_mode"] == "EQUITY"]
    crypto = [r["symbol"] for r in rows if r["market_mode"] == "CRYPTO"]

    return {"equity_symbols": equity, "crypto_symbols": crypto}

@router.get("/regimes")
async def get_regimes():
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM regime_memory ORDER BY updated_at DESC;")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"regimes": rows}
