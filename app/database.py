import os
from sqlmodel import create_engine, SQLModel, Session
from fastapi import Depends
from sqlalchemy import inspect, text
from typing import Generator

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:Admin12345@localhost:5432/tia_db")

engine = create_engine(DATABASE_URL, echo=True)

def init_db():
    SQLModel.metadata.create_all(engine)
    ensure_runtime_columns()

def ensure_runtime_columns():
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    timestamp_tables = ("users", "setoran_tahfizh")

    with engine.begin() as connection:
        # 1. Handling created_at timestamp
        for table_name in timestamp_tables:
            if table_name not in existing_tables:
                continue

            columns = {column["name"] for column in inspector.get_columns(table_name)}
            if "created_at" not in columns:
                connection.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                )

        # 2. Migration safety check untuk setoran_tahfizh (jumlah_tersendat & jumlah_teguran)
        if "setoran_tahfizh" in existing_tables:
            setoran_columns = {column["name"] for column in inspector.get_columns("setoran_tahfizh")}
            if "jumlah_tersendat" not in setoran_columns:
                connection.execute(text("ALTER TABLE setoran_tahfizh ADD COLUMN jumlah_tersendat INTEGER DEFAULT 0 NOT NULL"))
            if "jumlah_teguran" not in setoran_columns:
                connection.execute(text("ALTER TABLE setoran_tahfizh ADD COLUMN jumlah_teguran INTEGER DEFAULT 0 NOT NULL"))

        # 3. Migration safety check untuk quran_verses
        if "quran_verses" in existing_tables:
            quran_columns = {column["name"] for column in inspector.get_columns("quran_verses")}
            if "page_number" not in quran_columns:
                connection.execute(text("ALTER TABLE quran_verses ADD COLUMN page_number INTEGER DEFAULT 1"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_quran_verses_page_number ON quran_verses (page_number)"))
            if "juz_number" not in quran_columns:
                connection.execute(text("ALTER TABLE quran_verses ADD COLUMN juz_number INTEGER DEFAULT 1"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_quran_verses_juz_number ON quran_verses (juz_number)"))

        # 3. Migration safety check untuk santri foto profil & hasil ujian
        if "santri" in existing_tables:
            santri_columns = {column["name"] for column in inspector.get_columns("santri")}
            if "foto_profile" not in santri_columns:
                connection.execute(text("ALTER TABLE santri ADD COLUMN foto_profile VARCHAR DEFAULT NULL"))
            if "nilai_ujian" not in santri_columns:
                connection.execute(text("ALTER TABLE santri ADD COLUMN nilai_ujian FLOAT DEFAULT NULL"))
            if "hasil_ujian" not in santri_columns:
                connection.execute(text("ALTER TABLE santri ADD COLUMN hasil_ujian VARCHAR DEFAULT NULL"))

def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session