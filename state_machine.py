from enum import Enum

class DecisionState(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    FLAG_RISK = "FLAG_RISK"
    ACK = "ACK"
    VOIDED = "VOIDED"

TRANSITIONS = {
    "PENDING": {"CONFIRMED", "REJECTED", "FLAG_RISK", "VOIDED"},
    "CONFIRMED": {"ACK", "FLAG_RISK", "VOIDED"},
    "FLAG_RISK": {"CONFIRMED", "REJECTED", "VOIDED"},
    "ACK": {"FLAG_RISK", "VOIDED"},
}

ROLE_RULES = {
    "ADMIN": {"CONFIRMED", "REJECTED", "FLAG_RISK", "ACK", "VOIDED"},
    "CONFIRM": {"CONFIRMED", "FLAG_RISK", "ACK"},
}

def can_transition(current: str, target: str, role: str) -> bool:
    if current not in TRANSITIONS:
        return False
    if target not in TRANSITIONS[current]:
        return False
    if role not in ROLE_RULES:
        return False
    return target in ROLE_RULES[role]
