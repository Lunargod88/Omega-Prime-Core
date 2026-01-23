from fastapi import APIRouter, HTTPException, Header
from psycopg2.extras import RealDictCursor
import psycopg2
import os

from ai.analyzer import analyze_ledger

router = APIRouter(prefix="/negotiation", tags=["negotiation"])

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def resolve_identity(x_user_id: str | None, x_user_token: str | None) -> tuple[str, str]:
    from main import USER_TOKENS, USER_ROLES  # lazy import to avoid circular

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


@router.get("/status")
def negotiation_status():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM decision_ledger
        ORDER BY created_at DESC
        LIMIT 1;
        """
    )
    decision = cur.fetchone()
    cur.close()
    conn.close()

    analysis = analyze_ledger()

    return {
        "latest_decision": decision,
        "analysis": analysis,
    }


@router.post("/confirm/{decision_id}")
def confirm_decision(
    decision_id: int,
    x_user_id: str | None = Header(default=None),
    x_user_token: str | None = Header(default=None),
):
    uid, role = resolve_identity(x_user_id, x_user_token)

    if role not in {"CONFIRM", "ADMIN"}:
        raise HTTPException(status_code=403, detail="CONFIRM or ADMIN required")

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM decision_ledger WHERE id = %s;",
        (decision_id,),
    )
    row = cur.fetchone()

    if not row:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Decision not found")

    cur.execute(
        """
        UPDATE decision_ledger
        SET stance = 'CONFIRMED'
        WHERE id = %s;
        """,
        (decision_id,),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {
        "status": "confirmed",
        "decision_id": decision_id,
        "by": uid,
        "role": role,
  }
