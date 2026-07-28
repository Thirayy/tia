from datetime import date, datetime
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
    __table_args__ = {"extend_existing": True}
    
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True, nullable=False)
    password_hash: str = Field(nullable=False)
    nama_lengkap: str = Field(nullable=False)
    role: str = Field(default="musyrif", nullable=False) # 'admin' atau 'musyrif'
    created_at: Optional[datetime] = Field(default_factory=now_indonesia)

    # Relasi back-population
    kelompok: Optional["KelompokHalaqah"] = Relationship(back_populates="musyrif")


# ==========================================
# 3. TABEL KELOMPOK HALAQAH
# ==========================================
class KelompokHalaqah(SQLModel, table=True):
    __tablename__ = "kelompok_halaqah"
    __table_args__ = {"extend_existing": True}
    
    id: Optional[int] = Field(default=None, primary_key=True)
    nama_kelompok: str = Field(unique=True, nullable=False) # Contoh: "Halaqah Abu Bakar"
    musyrif_id: Optional[int] = Field(default=None, foreign_key="users.id")

    musyrif: Optional[User] = Relationship(back_populates="kelompok")
    santri_list: List["Santri"] = Relationship(back_populates="kelompok")


# ==========================================
# 4. TABEL SANTRI (Anak Didik)
# ==========================================
class Santri(SQLModel, table=True):
    __tablename__ = "santri"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    nama_santri: str
    nomor_induk: str
    kelompok_id: Optional[int] = Field(default=None, foreign_key="kelompok_halaqah.id")
    
    # ➕ TAMBAHKAN BARIS RELASI INI:
    kelompok: Optional[KelompokHalaqah] = Relationship(back_populates="santri_list")
    
    # Status & Izin
    status_santri: str = Field(default="hadir") # "hadir", "izin", "persiapan_ujian", "remed_ujian"
    keterangan_izin: Optional[str] = None
    
    # Target
    target_semester: Optional[str] = None        # Diisi oleh Admin (e.g. "Juz 30 Full")
    target_harian: Optional[str] = None          # Diisi oleh Musyrif (e.g. "1 Halaman / Hari")
    
    # Ujian
    tanggal_ujian: Optional[date] = None         # Tanggal pelaksanaan ujian
    catatan_persiapan_ujian: Optional[str] = None # Instruksi persiapan sampai hari H


# ==========================================
# 5. TABEL LOG STATUS SANTRI
# ==========================================
class StatusSantriLog(SQLModel, table=True):
    __tablename__ = "status_santri_logs"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    santri_id: int = Field(foreign_key="santri.id", index=True)
    status: str
    keterangan: Optional[str] = None
    created_at: datetime = Field(default_factory=now_indonesia)


# ==========================================
# 6. TABEL SETORAN TAHFIZH
# ==========================================
class SetoranTahfizh(SQLModel, table=True):
    __tablename__ = "setoran_tahfizh"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    santri_id: int = Field(foreign_key="santri.id", nullable=False)
    
    surah: str = Field(nullable=False)
    ayat: str = Field(nullable=False)
    status_kelancaran: str = Field(nullable=False)
    catatan_musyrif: Optional[str] = Field(default=None)
    created_at: Optional[datetime] = Field(default_factory=now_indonesia)
    ai_rekomendasi: Optional[str] = Field(default=None)


# ==========================================
# 7. TABEL HALAQAH DISRUPTION / LOG BADAL
# ==========================================
class HalaqahDisruption(SQLModel, table=True):
    __tablename__ = "halaqah_disruptions"
    __table_args__ = {"extend_existing": True}
    
    id: Optional[int] = Field(default=None, primary_key=True)
    tanggal: datetime = Field(default_factory=now_indonesia)
    kelompok_id: int = Field(foreign_key="kelompok_halaqah.id")
    musyrif_id: Optional[int] = Field(default=None, foreign_key="users.id")
    badal_musyrif_id: Optional[int] = Field(default=None, foreign_key="users.id")
    alasan: Optional[str] = None
    status_halaqah: str  # "diganti_badal", "diliburkan_total", "selesai"


# ==========================================
# 8. TABEL RAPORT SANTRI
# ==========================================
class RaportSantri(SQLModel, table=True):
    __tablename__ = "raport_santri"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    santri_id: int = Field(foreign_key="santri.id", index=True)
    semester: str  
    nilai_harian: float  
    nilai_bulanan: float 
    nilai_akhir: float   
    catatan_musyrif: Optional[str] = ""
    created_at: datetime = Field(default_factory=now_indonesia)


# ==========================================
# 9. TABEL QURAN KNOWLEDGE (RAG Data)
# ==========================================
class QuranKnowledge(SQLModel, table=True):
    __tablename__ = "quran_knowledge"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    kategori: str
    sub_kategori: str
    nama_parameter: str
    referensi_ayat: str
    teks_mentah_penjelasan: str

# ==========================================
# 10. TABEL QURAN PAGE
# ==========================================

class QuranPage(SQLModel, table=True):
    __tablename__ = "quran_pages"

    id: Optional[int] = Field(default=None, primary_key=True)
    page_number: int = Field(index=True)  # Halaman Madani (1 - 604)
    juz: int = Field(index=True)          # Juz (1 - 30)
    kaca: int = Field(index=True)         # Kaca dalam Juz (1 - 20)
    surah_id: int
    surah_name: str
    ayat_start: int
    ayat_end: int