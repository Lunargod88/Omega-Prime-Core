from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, validator
from typing import Optional

from models.enums import (
    StanceEnum,
    TierEnum,
    AuthorityEnum,
    ExitReasonEnum,
    RegimeEnum,
    ExitQualityEnum,
)

router = APIRouter(prefix="/ledger", tags=["ledger"])


class DecisionIngest(BaseModel):
    symbol: str
    timeframe: str

    stance: StanceEnum
    tier: TierEnum
    authority: AuthorityEnum = AuthorityEnum.NORMAL
    regime: RegimeEnum

    confidence: Optional[int] = None

    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    min_target: Optional[float] = None
    max_target: Optional[float] = None
    current_price: Optional[float] = None

    exit_reason: Optional[ExitReasonEnum] = ExitReasonEnum.NONE
    exit_quality: Optional[ExitQualityEnum] = None

    @validator("confidence")
    def validate_confidence(cls, v):
        if v is None:
            return v
        if not 0 <= v <= 100:
            raise ValueError("confidence must be between 0 and 100")
        return v


@router.post("/ingest")
async def ingest_decision(decision: DecisionIngest):
    # Placeholder until DB write is wired
    return {
        "status": "ok",
        "decision": decision.dict()
    }
