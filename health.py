import os
import uvicorn
from fastapi import FastAPI

app = FastAPI()

@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {"status": "ok"}

def make_config() -> uvicorn.Config:
    port = int(os.getenv("PORT", "8080"))
    return uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
