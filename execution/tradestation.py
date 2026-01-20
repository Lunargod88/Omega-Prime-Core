# execution/tradestation.py

def submit_paper_order(decision: dict):
    """
    PAPER MODE ONLY
    This does NOT place a real trade.
    """
    print("[PAPER EXECUTION]")
    print(decision)

    return {
        "status": "paper_submitted",
        "broker": "tradestation",
        "mode": "paper"
    }
