"""Mock Cenomi Sales Data Backend.

Runs on port 8001. Simulates the real cenomi-api endpoints needed by the
Sales Data Agent:

  GET    /api/v1/sales-data               — list rows (tenant-scoped)
  POST   /api/v1/sales-data               — bulk submit (all-or-nothing)
  PATCH  /api/v1/sales-data               — status update
  GET    /api/v1/sales-data/template      — CSV template download
  PUT    /files                           — generic file upload
  POST   /dev/mock/reset                  — reset to seed data
  GET    /dev/mock/state                  — inspect current DB

Usage:
  cd mock-backend
  pip install fastapi uvicorn python-multipart
  uvicorn main:app --reload --port 8001
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from seed_data import get_initial_state
from routers import sales_data, files, dev

app = FastAPI(
    title="Cenomi Sales Data Mock Backend",
    description="In-memory mock for the real cenomi-api sales data endpoints",
    version="1.0.0",
)

# Allow the frontend (Next.js on 3001) and AI backend (FastAPI on 8000) to call us
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    app.state.db = get_initial_state()


app.include_router(sales_data.router)
app.include_router(files.router)
app.include_router(dev.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mock-backend"}
