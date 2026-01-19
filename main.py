from fastapi import FastAPI
from pydantic import BaseModel
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

app = FastAPI(title="Ω PRIME Core")

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

@app.get("/health")
def health():
    return {"status": "ok"}

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
        confidence INTEGER NOT NULL,
        tier TEXT NOT NULL,
        reason TEXT,
        payload JSONB NOT NULL
    );
    """)

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "decision_ledger initialized"}
