from fastapi import FastAPI

app = FastAPI(title="Ω PRIME Core")

@app.get("/health")
def health():
    return {"status": "ok"}
