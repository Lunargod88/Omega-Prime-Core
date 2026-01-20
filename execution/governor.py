# execution/governor.py

OVERRIDE_USERS = {"YOU"}  # later: add wife, roles, etc.


def can_override(user: str) -> bool:
    return user in OVERRIDE_USERS
