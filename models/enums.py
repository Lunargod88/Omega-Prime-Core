from enum import Enum


# ==============================
# PHASE 1 — CANONICAL ENUMS
# ==============================

class Stance(str, Enum):
    ENTER_LONG = "ENTER_LONG"
    ENTER_SHORT = "ENTER_SHORT"
    HOLD_LONG = "HOLD_LONG"
    HOLD_SHORT = "HOLD_SHORT"
    HOLD_LONG_PAID = "HOLD_LONG_PAID"
    HOLD_SHORT_PAID = "HOLD_SHORT_PAID"
    STAND_DOWN = "STAND_DOWN"
    WAIT = "WAIT"


class Tier(str, Enum):
    S3 = "S+++"
    S2 = "S++"
    S1 = "S+"
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    ZERO = "Ø"


class Authority(str, Enum):
    NORMAL = "NORMAL"
    PRIME = "PRIME"


class Regime(str, Enum):
    COMPRESSION = "COMPRESSION"
    EXPANSION = "EXPANSION"
    NEUTRAL = "NEUTRAL"


class ExitReason(str, Enum):
    CRYPTO_TIMEOUT = "CRYPTO_TIMEOUT"
    DISTRIBUTION = "DISTRIBUTION"
    MOMENTUM_FADE = "MOMENTUM_FADE"
    REGIME_SHIFT = "REGIME_SHIFT"
    HTF_CONFLICT = "HTF_CONFLICT"
    TIME_DECAY = "TIME_DECAY"
    NONE = "NONE"


class ExitQuality(str, Enum):
    EARLY = "EARLY"
    GOOD = "GOOD"
    LATE = "LATE"
