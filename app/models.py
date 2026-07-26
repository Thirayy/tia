from datetime import datetime
from typing import Optional, List
from sqlmodel import Field, Relationship, SQLModel

from app.timezone import now_indonesia

# ==========================================
# 1. TABEL QURAN VERSES (RAG Reference Data)
# ==========================================
class QuranVerse(SQLModel, table=True):
    __tablename__ = "quran_verses"
    __table_args__ = {"extend_existing": True}
    
    id: Optional[int] = Field(default=None, primary_key=True)
    surah_id: int = Field(index=True)          # 1 - 114
    surah_name: str                            # Al-Baqarah, dll
    ayah_number: int = Field(index=True)       # Nomor ayat dalam surah
    page_number: int = Field(index=True)       # Halaman Mushaf Standar Madani (1 - 604)
    juz_number: int = Field(index=True)        # Juz (1 - 30)
    text_arabic: str                           # Teks Arab
    text_id: str                               # Terjemahan Indonesia
    tafsir_wajiz: Optional[str] = Field(default=None) # Tafsir Ringkas Kemenag


# ==========================================
# 2. TABEL USER (Admin & Musyrif)
# ==========================================
class User(SQLModel, table=True):
    __tablename__ = "users"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True, nullable=False)
    password_hash: str = Field(nullable=False)
    nama_lengkap: str = Field(nullable=False)
    role: str = Field(default="musyrif", nullable=False) # Isinya: 'admin' atau 'musyrif'
    created_at: Optional[datetime] = Field(default_factory=now_indonesia)

    # Relasi back-population
    kelompok: Optional["KelompokHalaqah"] = Relationship(back_populates="musyrif")


# ==========================================
# 3. TABEL KELOMPOK HALAQAH
# ==========================================
class KelompokHalaqah(SQLModel, table=True):
    __tablename__ = "kelompok_halaqah"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    nama_kelompok: str = Field(unique=True, nullable=False) # Misal: "Halaqah Abu Bakar"
    
    musyrif_id: Optional[int] = Field(default=None, foreign_key="users.id")

    musyrif: Optional[User] = Relationship(back_populates="kelompok")
    santri_list: List["Santri"] = Relationship(back_populates="kelompok")


# ==========================================
# 4. TABEL SANTRI (Anak Didik)
# ==========================================
class Santri(SQLModel, table=True):
    __tablename__ = "santri"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    nama_santri: str = Field(nullable=False)
    nomor_induk: str = Field(unique=True, nullable=False) # NISN / Nomor Induk
    status_santri: str = Field(default="aktif", nullable=False) # 'aktif' / 'alumni'
    
    kelompok_id: Optional[int] = Field(default=None, foreign_key="kelompok_halaqah.id")

    kelompok: Optional[KelompokHalaqah] = Relationship(back_populates="santri_list")


# ==========================================
# 5. TABEL SETORAN TAHFIZH
# ==========================================
class SetoranTahfizh(SQLModel, table=True):
    __tablename__ = "setoran_tahfizh"
    id: Optional[int] = Field(default=None, primary_key=True)
    santri_id: int = Field(foreign_key="santri.id", nullable=False)
    
    surah: str = Field(nullable=False)
    ayat: str = Field(nullable=False)
    status_kelancaran: str = Field(nullable=False)
    catatan_musyrif: Optional[str] = Field(default=None)
    created_at: Optional[datetime] = Field(default_factory=now_indonesia)
    
    ai_rekomendasi: Optional[str] = Field(default=None)

# ==========================================
# 6. TABEL QURAN KNOWLEDGE
# ==========================================
class QuranKnowledge(SQLModel, table=True):
    __tablename__ = "quran_knowledge"

    id: Optional[int] = Field(default=None, primary_key=True)
    kategori: str
    sub_kategori: str
    nama_parameter: str
    referensi_ayat: str
    teks_mentah_penjelasan: str