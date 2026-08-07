import os

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.database import init_db
from app.routes import auth, admin, musyrif
from app.routes.musyrif import router as musyrif_router
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file

app = FastAPI(
    title="TIA - Tahfizh Integrated Assessment",
    description="Backend API untuk sistem manajemen tahfizh pesantren",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Pastikan folder static yang dipakai tersedia
os.makedirs("static/uploads/profiles", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Sesuaikan dengan domain Anda
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://tia.khwarizmi.co.id")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:3000", "http://localhost:3002", "http://127.0.0.1:3002"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Router sudah benar, jangan tambah prefix /api
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(musyrif_router, prefix="/musyrif", tags=["musyrif"])

@app.get("/")
def read_root():
    return {"message": "Sistem API TIA Aktif!"}

@app.on_event("startup")
def on_startup():
    init_db()
    print("✅ Database initialized!")