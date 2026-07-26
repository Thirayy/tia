import os
import json
import re
from fastapi import APIRouter, Depends, HTTPException, Request, Header, Cookie
from sqlmodel import Session, select, SQLModel, Field
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date, time
from zoneinfo import ZoneInfo
import httpx

from app.database import get_session
from app.timezone import now_indonesia
from app.models import User, KelompokHalaqah, Santri, SetoranTahfizh
from sqlalchemy import func

from dotenv import load_dotenv

load_dotenv(override=True)  

# Konfigurasi OpenRouter
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = os.getenv("OPENROUTER_URL") or "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL") or "openrouter/free"

# Print log ini biar kelihatan jelas di terminal model apa yang beneran kebaca!
print(f"🚀 [INIT SYSTEM] OpenRouter Model Loaded: '{OPENROUTER_MODEL}'")
router = APIRouter()

# Timezone Asia/Jakarta (WIB)
WIB = ZoneInfo("Asia/Jakarta")

# ==========================================
# 0. TABEL DB TAMBAHAN (RAPORT & DISRUPSI)
# ==========================================
class HalaqahDisruption(SQLModel, table=True):
    __tablename__ = "halaqah_disruptions"
    __table_args__ = {"extend_existing": True} 
    
    id: Optional[int] = Field(default=None, primary_key=True)
    tanggal: datetime = Field(default_factory=now_indonesia)
    kelompok_id: int
    status_halaqah: str

class RaportSantri(SQLModel, table=True):
    __tablename__ = "raport_santri"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    santri_id: int = Field(foreign_key="santri.id", index=True)
    semester: str  # Contoh: "Ganjil 2026/2027" atau "1"
    nilai_harian: float  # NH
    nilai_bulanan: float # NB
    nilai_akhir: float   # NA
    catatan_musyrif: Optional[str] = ""
    created_at: datetime = Field(default_factory=now_indonesia)

# ==========================================
# SCHEMAS (PYDANTIC)
# ==========================================
class VoiceParseRequest(BaseModel):
    voice_command_text: str

class SetoranCreate(BaseModel):
    santri_id: Optional[int] = Field(default=None, alias="student_id") # Menerima santri_id maupun student_id
    surah_id: Optional[int] = None
    surah_sahih: Optional[str] = None
    surah: Optional[str] = None
    ayat_standar: Optional[str] = None
    ayat: Optional[str] = None
    status_kelancaran: str = "Lancar"
    catatan_koreksi: Optional[str] = None
    ada_teguran: Optional[bool] = False
    jenis_teguran: Optional[str] = None
    catatan_teguran: Optional[str] = None
    catatan_musyrif: Optional[str] = ""

    class Config:
        populate_by_name = True

class RangkumanHarianRequest(BaseModel):
    tanggal: Optional[str] = None  # Format: "YYYY-MM-DD", jika None default hari ini WIB

class RaportCreate(BaseModel):
    santri_id: int
    semester: str
    nilai_harian: float
    nilai_bulanan: float
    nilai_akhir: float
    catatan_musyrif: Optional[str] = ""

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def get_session_username(request: Request, session_user: Optional[str] = None) -> Optional[str]:
    return session_user or request.headers.get("x-session-user") or request.cookies.get("x-session-user")

# ==========================================
# 1. GET DAFTAR SANTRI BINAAN
# ==========================================
@router.get("/santri")
def get_all_santri(
    request: Request,
    session: Session = Depends(get_session),
    session_user: Optional[str] = Cookie(None)
):
    username = get_session_username(request, session_user)
    if not username:
        raise HTTPException(status_code=401, detail="Belum login!")
    
    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User session gak valid!")
    
    santri_list = []
    nama_ustadz = getattr(user, "nama_lengkap", user.username)

    if user.role == "admin":
        santri_list = session.exec(select(Santri)).all()
    elif user.role == "musyrif":
        kelompok = session.exec(select(KelompokHalaqah).where(KelompokHalaqah.musyrif_id == user.id)).first()
        if not kelompok:
            return {"status": "success", "nama_ustadz": nama_ustadz, "data": [], "santri": []}
            
        santri_list = session.exec(select(Santri).where(Santri.kelompok_id == kelompok.id)).all()
    else:
        raise HTTPException(status_code=403, detail="Role tidak diizinkan!")

    formatted_santri = [
        {
            "id": s.id,
            "nama_santri": s.nama_santri,
            "nomor_induk": getattr(s, "nomor_induk", "")
        } for s in santri_list
    ]

    return {
        "status": "success",
        "nama_ustadz": nama_ustadz,
        "data": formatted_santri,
        "santri": formatted_santri
    }

# ==========================================
# 2. VOICE PARSER (Hanya Ekstrak Surah, Ayat, & Teguran)
# ==========================================
@router.post("/parse-voice")
async def parse_voice_command(data: VoiceParseRequest):
    """
    Endpoint ini HANYA mengekstrak ucapan musyrif (Surah, Ayat, Kelancaran, & Teguran/Catatan).
    Tidak perlu deteksi nama santri karena santri sudah dipilih di UI Frontend.
    """
    system_prompt = """
    Kamu adalah Engine AI Parser untuk Sistem Mutabaah Tahfizh Quran.
    Musyrif sedang menyimak setoran dan memberikan instruksi/teguran/catatan via suara.

    Tugas Utama: Ekstrak ucapan Musyrif menjadi JSON draf.

    Aturan Parsing:
    1. Hafalan:
       - surah_sahih: Nama Surah (contoh: "Al-Ikhlas", "Al-Baqarah", "An-Nas").
       - surah_id: Nomor surah Al-Quran 1-114 (contoh: Al-Baqarah = 2, Al-Ikhlas = 112).
       - ayat_standar: Rentang ayat (contoh: "1-4", "1-10").
       - status_kelancaran: Pilih salah satu dari ["Lancar", "Cukup Lancar", "Kurang Lancar", "Mengulang"]. (Default: "Lancar" jika tidak disebutkan).

    2. Teguran & Catatan Musyrif:
       - catatan_koreksi: Catatan makhraj, tajwid, atau kesalahan bacaan.
       - ada_teguran: boolean (true jika ada teguran adab/kedisiplinan/sikap, false jika tidak ada).
       - jenis_teguran: ["Tajwid/Makhraj", "Adab", "Kedisiplinan", "Lainnya"] (null jika tidak ada).
       - catatan_teguran: Detail teguran dari musyrif (null jika tidak ada).

    OUTPUT WAJIB FORMAT JSON MURNI:
    {
        "surah_id": 112,
        "surah_sahih": "Al-Ikhlas",
        "ayat_standar": "1-4",
        "status_kelancaran": "Lancar",
        "catatan_koreksi": "Makhraj huruf Ain masih kurang bersih",
        "ada_teguran": true,
        "jenis_teguran": "Adab",
        "catatan_teguran": "Sempat ngobrol dan tidak fokus saat halaqah"
    }
    """

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Ucapan Musyrif: \"{data.voice_command_text}\""}
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"}
    }

    try:
        if OPENROUTER_API_KEY:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
                if resp.status_code == 200:
                    content = resp.json()['choices'][0]['message']['content']
                    return {"status": "success", "draft": json.loads(content)}
                else:
                    print(f"❌ OPENROUTER ERROR: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"❌ VOICE PARSE ERROR: {e}")

    # Fallback jika API sedang offline / error
    return {
        "status": "success",
        "draft": {
            "surah_id": 112,
            "surah_sahih": "Al-Ikhlas",
            "ayat_standar": "1-4",
            "status_kelancaran": "Lancar",
            "catatan_koreksi": data.voice_command_text,
            "ada_teguran": False,
            "jenis_teguran": None,
            "catatan_teguran": None
        }
    }

# ==========================================
# 3. POST SIMPAN SETORAN (Setelah Edit/Verifikasi Manual)
# ==========================================
@router.post("/setoran")
async def input_setoran(
    request: Request,
    data: SetoranCreate,
    session: Session = Depends(get_session),
    session_user: Optional[str] = Cookie(None) 
):
    username = get_session_username(request, session_user)
    if not username:
        raise HTTPException(status_code=401, detail="Session expired")

    # Ambil santri_id dari 'santri_id' atau 'student_id'
    target_santri_id = data.santri_id
    if not target_santri_id:
        raise HTTPException(status_code=422, detail="Field santri_id / student_id wajib diisi!")

    user = session.exec(select(User).where(User.username == username)).first()
    santri = session.exec(select(Santri).where(Santri.id == target_santri_id)).first()
    
    if not user or not santri:
        raise HTTPException(status_code=404, detail="User atau Santri tidak ditemukan")

    # Ambil nama surah dan ayat secara fleksibel dari payload
    final_surah = data.surah_sahih or data.surah or f"Surah ID: {data.surah_id}"
    final_ayat = data.ayat_standar or data.ayat or "-"
    
    # Gabungkan catatan koreksi & teguran adab ke catatan_musyrif
    notes = []
    if data.catatan_koreksi:
        notes.append(f"Koreksi: {data.catatan_koreksi}")
    if data.ada_teguran and data.catatan_teguran:
        notes.append(f"Teguran ({data.jenis_teguran or 'Adab'}): {data.catatan_teguran}")
    if data.catatan_musyrif:
        notes.append(data.catatan_musyrif)
        
    combined_notes = " | ".join(notes) if notes else ""

    now_wib = now_indonesia()

    new_setoran = SetoranTahfizh(
        santri_id=target_santri_id,
        surah=final_surah,
        ayat=final_ayat,
        status_kelancaran=data.status_kelancaran,
        catatan_musyrif=combined_notes,
        ai_rekomendasi=f"Input terverifikasi. Tanggal: {now_wib.strftime('%d-%m-%Y %H:%M WIB')}"
    )
    session.add(new_setoran)
    session.commit()
    session.refresh(new_setoran)

    return {"status": "success", "message": "Setoran berhasil dicatat!", "data": new_setoran}
# ==========================================
# 4. GET HISTORI & AI RANGKUMAN HARIAN (WIB Filtered)
# ==========================================
@router.post("/ai/rangkuman-harian/{santri_id}")
async def generate_rangkuman_harian(
    santri_id: int, 
    req: RangkumanHarianRequest, 
    session: Session = Depends(get_session)
):
    """
    Menghasilkan Rangkuman AI KHUSUS untuk tanggal/hari tertentu (Default: Hari Ini WIB).
    """
    santri = session.get(Santri, santri_id)
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan")

    # Tentukan rentang waktu WIB untuk tanggal tersebut (00:00:00 s/d 23:59:59)
    if req.tanggal:
        target_date = datetime.strptime(req.tanggal, "%Y-%m-%d").date()
    else:
        target_date = now_indonesia().date()

    start_of_day = datetime.combine(target_date, time.min, tzinfo=WIB)
    end_of_day = datetime.combine(target_date, time.max, tzinfo=WIB)

    # Tarik data setoran HANYA pada tanggal tersebut
    setoran_today = session.exec(
        select(SetoranTahfizh)
        .where(SetoranTahfizh.santri_id == santri_id)
        .where(SetoranTahfizh.created_at >= start_of_day)
        .where(SetoranTahfizh.created_at <= end_of_day)
    ).all()

    if not setoran_today:
        return {
            "status": "success",
            "tanggal": target_date.strftime("%Y-%m-%d"),
            "rangkuman_ai": f"Tidak ada catatan setoran untuk tanggal {target_date.strftime('%d %B %Y')}."
        }

    catatan_teks = "\n".join([
        f"- Surah {s.surah}:{s.ayat} ({s.status_kelancaran}) | Catatan: {s.catatan_musyrif or 'Kosong'}"
        for s in setoran_today
    ])

    prompt = f"""
    Tugas: Buat Rangkuman Harian Sesi Halaqah Santri berikut secara padat dan jelas.
    Santri: {santri.nama_santri}
    Tanggal: {target_date.strftime('%d %B %Y')}
    Catatan Sesi Hari Ini:
    {catatan_teks}

    Format Rangkuman:
    - Sesi Hari Ini: [Ringkasan singkat apa yang disetorkan]
    - Catatan Evaluasi: [Poin kekurang/kelebihan hari ini]
    """

    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": "Kamu adalah AI Note-Taker & Rangkuman Halaqah."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2
    }

    rangkuman = "Gagal memproses rangkuman."
    try:
        if OPENROUTER_API_KEY:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
                if resp.status_code == 200:
                    rangkuman = resp.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        rangkuman = f"Error koneksi AI: {e}"

    return {
        "status": "success",
        "tanggal": target_date.strftime("%Y-%m-%d"),
        "total_setoran_hari_ini": len(setoran_today),
        "rangkuman_ai": rangkuman
    }

# ==========================================
# 5. AI ANALISIS OVERALL (Semua Laporan Santri)
# ==========================================
@router.post("/statistik/analisis/{santri_id}")
async def analyze_overall_santri(santri_id: int, session: Session = Depends(get_session)):
    """
    Analisis AI mendalam untuk KESELURUHAN histori setoran santri dari awal sampai sekarang.
    """
    santri = session.get(Santri, santri_id)
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan!")

    setoran_list = session.exec(
        select(SetoranTahfizh)
        .where(SetoranTahfizh.santri_id == santri_id)
        .order_by(SetoranTahfizh.id.asc()) 
    ).all()
    
    if not setoran_list:
        return {
            "status": "success",
            "data": {
                "nama_santri": santri.nama_santri,
                "analisis_ai": "Belum ada data setoran sama sekali untuk dianalisis."
            }
        }
    
    riwayat_teks = "\n".join([
        f"- [{s.created_at.strftime('%d/%m/%Y')}] Surah {s.surah}:{s.ayat} ({s.status_kelancaran}) | Catatan: {s.catatan_musyrif or '-'}" 
        for s in setoran_list
    ])
    
    prompt_analisis = f"""
    Peran: Senior Head of Tahfizh.
    Tugas: Analisis grafik perkembangan hafalan santri berdasarkan SELURUH histori setoran berikut.

    Nama Santri: {santri.nama_santri}
    Histori Semua Setoran:
    {riwayat_teks}

    Format Jawaban:
    Ringkasan Perkembangan:
    [Tren perkembangan hafalan]

    Poin Kekuatan:
    [Konsistensi/Kelancaran]

    Area Perbaikan:
    [Ayat/Surah yang sering tersendat]

    Rekomendasi Musyrif:
    1. [Langkah perbaikan 1]
    2. [Langkah perbaikan 2]
    """

    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": OPENROUTER_MODEL, 
        "messages": [
            {"role": "system", "content": "Kamu adalah AI Analyst Perkembangan Tahfizh."},
            {"role": "user", "content": prompt_analisis}
        ],
        "temperature": 0.3
    }

    analisis_ai = "Gagal memproses analisis."
    try:
        if OPENROUTER_API_KEY:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
                if resp.status_code == 200:
                    analisis_ai = resp.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        analisis_ai = f"Error koneksi AI: {e}"

    return {
        "status": "success",
        "data": {
            "nama_santri": santri.nama_santri,
            "analisis_ai": analisis_ai
        }
    }

# ==========================================
# 6. FORM NILAI RAPORT SEMESTER (NH, NB, NA)
# ==========================================
@router.post("/raport")
def submit_nilai_raport(data: RaportCreate, session: Session = Depends(get_session)):
    """
    Endpoint untuk menginput Nilai Harian (NH), Nilai Bulanan (NB), dan Nilai Akhir (NA) Raport.
    """
    santri = session.get(Santri, data.santri_id)
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan")

    # Cek apakah raport semester ini sudah ada, jika ada maka update
    existing_raport = session.exec(
        select(RaportSantri)
        .where(RaportSantri.santri_id == data.santri_id)
        .where(RaportSantri.semester == data.semester)
    ).first()

    if existing_raport:
        existing_raport.nilai_harian = data.nilai_harian
        existing_raport.nilai_bulanan = data.nilai_bulanan
        existing_raport.nilai_akhir = data.nilai_akhir
        existing_raport.catatan_musyrif = data.catatan_musyrif
        session.add(existing_raport)
        session.commit()
        session.refresh(existing_raport)
        return {"status": "success", "message": "Nilai Raport berhasil diperbarui!", "data": existing_raport}

    new_raport = RaportSantri(
        santri_id=data.santri_id,
        semester=data.semester,
        nilai_harian=data.nilai_harian,
        nilai_bulanan=data.nilai_bulanan,
        nilai_akhir=data.nilai_akhir,
        catatan_musyrif=data.catatan_musyrif
    )
    session.add(new_raport)
    session.commit()
    session.refresh(new_raport)

    return {"status": "success", "message": "Nilai Raport berhasil disimpan!", "data": new_raport}


@router.get("/raport/{santri_id}")
def get_nilai_raport(santri_id: int, session: Session = Depends(get_session)):
    """
    Mendapatkan daftar nilai raport santri per semester.
    """
    raports = session.exec(
        select(RaportSantri)
        .where(RaportSantri.santri_id == santri_id)
        .order_by(RaportSantri.id.desc())
    ).all()

    return {"status": "success", "data": raports}

# ==========================================
# 7. GET PROFILE DETAIL SANTRI (Bisa Akses dari Musyrif & Admin)
# ==========================================
@router.get("/santri/{santri_id}/profile")
def get_profile_santri(
    santri_id: int,
    session: Session = Depends(get_session)
):
    santri = session.get(Santri, santri_id)
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan!")

    # Detail Kelompok & Musyrif
    kelompok = session.get(KelompokHalaqah, santri.kelompok_id) if santri.kelompok_id else None
    musyrif = session.get(User, kelompok.musyrif_id) if (kelompok and kelompok.musyrif_id) else None

    # SEMUA Histori Setoran Santri Ini (TANPA LIMIT)
    setoran_list = session.exec(
        select(SetoranTahfizh)
        .where(SetoranTahfizh.santri_id == santri_id)
        .order_by(SetoranTahfizh.id.desc())
    ).all()

    # Nilai Raport Santri Ini
    raport_list = session.exec(
        select(RaportSantri)
        .where(RaportSantri.santri_id == santri_id)
        .order_by(RaportSantri.id.desc())
    ).all()

    from app.timezone import format_indonesia

    return {
        "status": "success",
        "profile": {
            "id": santri.id,
            "nama_santri": santri.nama_santri,
            "nomor_induk": getattr(santri, 'nomor_induk', '-'),
            "status_santri": getattr(santri, 'status_santri', 'aktif') or 'aktif',
            "kelompok_id": santri.kelompok_id,
            "nama_kelompok": kelompok.nama_kelompok if kelompok else "Belum Masuk Kelompok",
            "musyrif_id": musyrif.id if musyrif else None,
            "nama_musyrif": musyrif.nama_lengkap if musyrif else "Belum Diplotting",
            "total_setoran": len(setoran_list)
        },
        "histori_setoran_full": [
            {
                "id": s.id,
                "surah": s.surah,
                "ayat": s.ayat,
                "status_kelancaran": s.status_kelancaran,
                "catatan_musyrif": getattr(s, 'catatan_musyrif', ''),
                "waktu_setoran": format_indonesia(getattr(s, 'created_at', None))
            } for s in setoran_list
        ],
        "raport": raport_list
    }