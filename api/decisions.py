from fastapi import APIRouter, HTTPException, Query
from typing import Optional, Literal
import os
import psycopg2
from psycopg2.extras import RealDictCursor

router = APIRouter(prefix="/api", tags=["decisions"])

Account = Literal["JAYLYN", "WIFE"]

def _db_conn():
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(dsn)

@router.get("/decisions")
def list_decisions(
    account: Optional[Account] = Query(default=None),
    symbol: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    """
    PHASE 3: Dashboard pulls decisions WITH price context.
    """
    where = []
    params = {}

    if account:
        where.append("account = %(account)s")
        params["account"] = account
    if symbol:
        where.append("symbol = %(symbol)s")
        params["symbol"] = symbol

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT
            id,
            created_at,
            account,
            symbol,
            timeframe,
            stance,
            tier,
            authority,
            confidence,
            regime,
            entry_price,
            stop_price,
            min_target,
            max_target,
            current_price
        FROM decision_ledger
        {where_sql}
        ORDER BY created_at DESC
        LIMIT %(limit)s
    """
    params["limit"] = limit

    try:
        with _db_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB query failed: {e}")

    return {"items": rows}
