from datetime import datetime
from typing import Optional, List
from sqlmodel import Field, Relationship, SQLModel

from app.timezone import now_indonesia

# ==========================================
# 1. TABEL USER (Admin & Musyrif)
# ==========================================
class User(SQLModel, table=True):
    __tablename__ = "users"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True, nullable=False)
    password_hash: str = Field(nullable=False)
    nama_lengkap: str = Field(nullable=False)
    role: str = Field(default="musyrif", nullable=False) # Isinya: 'admin' atau 'musyrif'
    created_at: Optional[datetime] = Field(default_factory=now_indonesia)

    # Relasi back-population: Satu musyrif mengelola satu kelompok halaqah
    kelompok: Optional["KelompokHalaqah"] = Relationship(back_populates="musyrif")


# ==========================================
# 2. TABEL KELOMPOK HALAQAH (Mapping Jembatan)
# ==========================================
class KelompokHalaqah(SQLModel, table=True):
    __tablename__ = "kelompok_halaqah"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    nama_kelompok: str = Field(unique=True, nullable=False) # Misal: "Halaqah Abu Bakar"
    
    # Foreign Key ke tabel users (siapa musyrifnya)
    musyrif_id: Optional[int] = Field(default=None, foreign_key="users.id")

    # Relasi penghubung ke model User dan Santri
    musyrif: Optional[User] = Relationship(back_populates="kelompok")
    santri_list: List["Santri"] = Relationship(back_populates="kelompok")


# ==========================================
# 3. TABEL SANTRI (Anak Didik)
# ==========================================
class Santri(SQLModel, table=True):
    __tablename__ = "santri"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    nama_santri: str = Field(nullable=False)
    nomor_induk: str = Field(unique=True, nullable=False) # Misal: NISN / Nomor Induk Pondok
    status_santri: str = Field(default="aktif", nullable=False) # Isinya: 'aktif' atau 'alumni'
    
    # Foreign Key ke kelompok halaqah tempat dia belajar
    kelompok_id: Optional[int] = Field(default=None, foreign_key="kelompok_halaqah.id")

    # Relasi back-population ke kelompok
    kelompok: Optional[KelompokHalaqah] = Relationship(back_populates="santri_list")

# ==========================================
# 4. TABEL SETORAN TAHFIZH (Histori & AI Assessment)
# ==========================================
class SetoranTahfizh(SQLModel, table=True):
    __tablename__ = "setoran_tahfizh"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    santri_id: int = Field(foreign_key="santri.id", nullable=False)
    
    # Detail Setoran
    surah: str = Field(nullable=False) # Misal: "Al-Baqarah"
    ayat: str = Field(nullable=False)  # Misal: "1-10"
    status_kelancaran: str = Field(nullable=False) # Isinya: 'lancar', 'sedang', 'kurang'
    catatan_musyrif: Optional[str] = Field(default=None) # Catatan manual ustadz
    created_at: Optional[datetime] = Field(default_factory=now_indonesia)
    
    # Kolom Sakti: Output Analisis dari Llama 3
    ai_rekomendasi: Optional[str] = Field(default=None)
