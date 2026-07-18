import os
from sqlmodel import create_engine, SQLModel, Session
from fastapi import Depends
from typing import Generator

# =========================================================================
# KONEKSI DATABASE POSTGRESQL
# Format: postgresql://username:password@localhost:50000/nama_db
# 💡 Tips: Silahkan ganti username, password, port, dan nama_db sesuai settingan lokal
# =========================================================================
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://admin:Admin12345@localhost:5432/tia_db")

# Buat engine koneksi
engine = create_engine(DATABASE_URL, echo=True) # echo=True biar bisa liat log query SQL asli di terminal pas testing

# Fungsi untuk inisialisasi tabel otomatis pas aplikasi pertama kali jalan
def init_db():
    SQLModel.metadata.create_all(engine)

# Dependency untuk inject DB Session ke endpoint FastAPI
def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
