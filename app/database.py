import os
from sqlmodel import create_engine, SQLModel, Session
from fastapi import Depends
from sqlalchemy import inspect, text
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
    ensure_runtime_columns()


def ensure_runtime_columns():
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    timestamp_tables = ("users", "setoran_tahfizh")

    with engine.begin() as connection:
        for table_name in timestamp_tables:
            if table_name not in existing_tables:
                continue

            columns = {column["name"] for column in inspector.get_columns(table_name)}
            if "created_at" not in columns:
                connection.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                )

# Dependency untuk inject DB Session ke endpoint FastAPI
def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
