@router.get("/symbols")
async def get_symbols():
    rows = await database.fetch_all("SELECT symbol, market_mode FROM symbol_whitelist")
    equity = [r["symbol"] for r in rows if r["market_mode"] == "EQUITY"]
    crypto = [r["symbol"] for r in rows if r["market_mode"] == "CRYPTO"]
    return {"equity_symbols": equity, "crypto_symbols": crypto}
