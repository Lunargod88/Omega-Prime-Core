from enum import Enum


class StanceEnum(str, Enum):
    ENTER_LONG = "ENTER_LONG"
    ENTER_SHORT = "ENTER_SHORT"
    HOLD_LONG = "HOLD_LONG"
    HOLD_SHORT = "HOLD_SHORT"
    HOLD_LONG_PAID = "HOLD_LONG_PAID"
    HOLD_SHORT_PAID = "HOLD_SHORT_PAID"
    STAND_DOWN = "STAND_DOWN"
    WAIT = "WAIT"


class TierEnum(str, Enum):
    S3 = "S+++"
    S2 = "S++"
    S1 = "S+"
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    ZERO = "Ø"   # was Ø, renamed safely


class AuthorityEnum(str, Enum):
    NORMAL = "NORMAL"
    PRIME = "PRIME"


class ExitReasonEnum(str, Enum):
    CRYPTO_TIMEOUT = "CRYPTO_TIMEOUT"
    DISTRIBUTION = "DISTRIBUTION"
    MOMENTUM_FADE = "MOMENTUM_FADE"
    REGIME_SHIFT = "REGIME_SHIFT"
    HTF_CONFLICT = "HTF_CONFLICT"
    TIME_DECAY = "TIME_DECAY"
    NONE = "NONE"


class RegimeEnum(str, Enum):
    COMPRESSION = "COMPRESSION"
    EXPANSION = "EXPANSION"
    NEUTRAL = "NEUTRAL"


class ExitQualityEnum(str, Enum):
    EARLY = "EARLY"
    GOOD = "GOOD"
    LATE = "LATE"
