from fastapi import FastAPI

app = FastAPI(title="Bayan Test API")


@app.get("/")
async def home():
    return {
        "status": "online",
        "service": "Bayan Test API",
        "message": "Build test successful"
    }


@app.get("/test")
async def test():
    return {
        "ok": True
    }
