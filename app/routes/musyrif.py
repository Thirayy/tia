import os
import json
import httpx
import re
from fastapi import APIRouter, Depends, HTTPException, Request, Cookie
from sqlmodel import Session, select
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime, date, time
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from app.database import get_session
from app.timezone import now_indonesia, format_indonesia
from app.models import (
    User, 
    KelompokHalaqah, 
    Santri, 
    SetoranTahfizh, 
    StatusSantriLog, 
    RaportSantri,
    QuranPage
)

load_dotenv(override=True)  

# Konfigurasi OpenRouter
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = os.getenv("OPENROUTER_URL") or "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL") or "openrouter/free"

print(f"🚀 [INIT SYSTEM] OpenRouter Model Loaded: '{OPENROUTER_MODEL}'")
router = APIRouter()

WIB = ZoneInfo("Asia/Jakarta")

# ==========================================
# SCHEMAS (PYDANTIC)
# ==========================================
class VoiceParseRequest(BaseModel):
    voice_command_text: str

class SetoranCreate(BaseModel):
    santri_id: Optional[int] = Field(default=None, alias="student_id")
    surah_id: Optional[int] = None
    surah_sahih: Optional[str] = None
    surah: Optional[str] = None
    ayat_standar: Optional[str] = None
    ayat: Optional[str] = None
    status_kelancaran: str = "Lancar"
    catatan_koreksi: Optional[str] = None
    ada_teguran: Optional[bool] = False
    jenis_teguran: Optional[List[str]] = Field(default_factory=list) # <--- Ubah jadi List[str]
    catatan_teguran: Optional[str] = None
    catatan_musyrif: Optional[str] = ""

    class Config:
        populate_by_name = True

class TargetMusyrifPayload(BaseModel):
    target_harian: str

class UpdateStatusPayload(BaseModel):
    status: str  # "hadir", "izin", "persiapan_ujian", "remed_ujian"
    keterangan: Optional[str] = None 

class DaftarUjianPayload(BaseModel):
    tanggal_ujian: date
    catatan_persiapan: Optional[str] = None

class HasilUjianPayload(BaseModel):
    hasil: Literal["lulus", "remed"]
    catatan: Optional[str] = None

class RangkumanHarianRequest(BaseModel):
    tanggal: Optional[str] = None

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
# DATABASE LOOKUP QURAN (DARI TABEL DB)
# ==========================================
def get_quran_mapping_from_db(
    session: Session, 
    juz: Optional[int], 
    kaca: Optional[int], 
    bagian: Optional[str] = None
) -> dict:
    if not juz or not kaca:
        return {"surah_id": None, "surah_sahih": None, "ayat_standar": "-"}

    # Query ke tabel database berdasarkan Juz & Kaca
    mapping = session.exec(
        select(QuranPage)
        .where(QuranPage.juz == juz)
        .where(QuranPage.kaca == kaca)
    ).first()

    # Fallback kalau data halaman tersebut belum di-seed di database
    if not mapping:
        return {
            "surah_id": None, 
            "surah_sahih": None, 
            "ayat_standar": "-"
        }

    a_start, a_end = mapping.ayat_start, mapping.ayat_end

    # Logika Pecahan A/B (Paruh Atas / Paruh Bawah)
    if bagian == "a":
        mid = a_start + (a_end - a_start) // 2
        a_range = f"{a_start}" if a_start == mid else f"{a_start}-{mid}"
    elif bagian == "b":
        mid = a_start + (a_end - a_start) // 2 + 1
        a_range = f"{a_end}" if mid >= a_end else f"{mid}-{a_end}"
    else:
        a_range = f"{a_start}" if a_start == a_end else f"{a_start}-{a_end}"

    return {
        "surah_id": mapping.surah_id,
        "surah_sahih": mapping.surah_name,
        "ayat_standar": a_range
    }

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
        raise HTTPException(status_code=404, detail="User session tidak valid!")
    
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
            "nomor_induk": getattr(s, "nomor_induk", ""),
            "status_santri": getattr(s, "status_santri", "hadir"),
            "target_harian": getattr(s, "target_harian", None)
        } for s in santri_list
    ]

    return {
        "status": "success",
        "nama_ustadz": nama_ustadz,
        "data": formatted_santri,
        "santri": formatted_santri
    }


@router.post("/parse-voice")
async def parse_voice_command(
    data: VoiceParseRequest, 
    session: Session = Depends(get_session)
):
    system_prompt = """
Kamu adalah Ekstraktor Catatan Suara Realtime Halaqah (Live Session Assitant).
Musyrif sedang MENYIMAK SANTRI SECARA LIVE dan mendiktekan koreksi/teguran saat itu juga di tempat setoran.

PRINSIPIAL PARSING REALTIME:
1. SETORAN JUZ & KACA:
   - juz: integer 1-30 / null
   - kaca: integer nomor halaman / null
   - bagian: "a" (paruh atas/awal) / "b" (paruh bawah/akhir) / null
   - surah_langsung & ayat_langsung: Isi jika musyrif menyebut surah murni (contoh: "Al-Mulk 1-15").

2. ELSPLISIT KOREKSI TAJWID & MAKHRAJ (INSTAN):
   - status_kelancaran: Pilih dari ["Lancar", "Cukup Lancar", "Kurang Lancar", "Mengulang"]. Default: "Lancar".
   - catatan_koreksi: Tangkap langsung kesalahan bacaan saat itu juga (contoh: "ghunnah kurang tahan", "makhraj ain tertukar", "qalqalah kurang pantul", "mad kurang panjang").

3. LOGIKA CLUE / BISIKAN INGATAN (LIVE ASSIST):
   - Hitung angka bantuan clue/lupa yang diucapkan musyrif (contoh: "clue 7x", "dibisikin 7 kali", "potongan ayat 7x").
   - jumlah_clue_ingatan: integer.
   - JIKA jumlah_clue_ingatan >= 7, OTOMATIS masukkan "Ingatan" ke array `jenis_teguran`.

4. TEGURAN MULTI-KATEGORI (ARRAY):
   - jenis_teguran: Array dari ["Tajwid/Makhraj", "Ingatan", "Adab", "Kedisiplinan", "Lainnya"].
   - Jika ada koreksi tajwid/makhraj -> Sertakan "Tajwid/Makhraj".
   - Jika jumlah_clue_ingatan >= 7 -> Sertakan "Ingatan".
   - Jika musyrif menegur sikap duduk/adab (contoh: "mainan peci", "ngobrol", "tidak sopan") -> Sertakan "Adab".
   - catatan_teguran: Isi poin teguran adab/kedisiplinan realtime tersebut.
   - ada_teguran: boolean (true jika array `jenis_teguran` tidak kosong).

KELUARKAN HASIL HANYA DALAM FORMAT JSON:
{
    "juz": null,
    "kaca": null,
    "bagian": null,
    "surah_langsung": null,
    "ayat_langsung": null,
    "status_kelancaran": "Lancar",
    "catatan_koreksi": null,
    "jumlah_clue_ingatan": 0,
    "ada_teguran": false,
    "jenis_teguran": [],
    "catatan_teguran": null,
    "catatan_musyrif": null
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
            {"role": "user", "content": f"Ucapan Live Musyrif: \"{data.voice_command_text}\""}
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"}
    }

    try:
        if OPENROUTER_API_KEY:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
                
                if resp.status_code == 200:
                    res_json = resp.json()
                    content = res_json['choices'][0]['message']['content'] if 'choices' in res_json and res_json['choices'] else ""
                    
                    # Sanitasi JSON String
                    content_clean = content.strip()
                    if content_clean.startswith("```json"):
                        content_clean = content_clean[7:]
                    if content_clean.startswith("```"):
                        content_clean = content_clean[3:]
                    if content_clean.endswith("```"):
                        content_clean = content_clean[:-3]
                    content_clean = content_clean.strip()

                    if not content_clean:
                        raise ValueError("Response dari OpenRouter kosong")

                    raw_parsed = json.loads(content_clean)
            
                    # Ekstrak Nomor Kaca & Bagian A/B
                    raw_kaca = raw_parsed.get("kaca")
                    bagian_val = str(raw_parsed.get("bagian")).lower() if raw_parsed.get("bagian") else None
                    kaca_clean = None

                    if raw_kaca is not None:
                        match = re.search(r'(\d+)\s*([a-bA-B]?)', str(raw_kaca))
                        if match:
                            kaca_clean = int(match.group(1))
                            if match.group(2) and not bagian_val:
                                bagian_val = match.group(2).lower()

                    # Lookup Surah & Ayat dari DB Quran
                    if raw_parsed.get("surah_langsung"):
                        surah_info = {
                            "surah_id": None,
                            "surah_sahih": raw_parsed.get("surah_langsung"),
                            "ayat_standar": raw_parsed.get("ayat_langsung") or "-"
                        }
                    else:
                        surah_info = get_quran_mapping_from_db(
                            session=session,
                            juz=raw_parsed.get("juz"),
                            kaca=kaca_clean,
                            bagian=bagian_val
                        )

                    # Handover multi-teguran & threshold clue ingatan
                    jenis_teguran_list = raw_parsed.get("jenis_teguran", [])
                    if isinstance(jenis_teguran_list, str):
                        jenis_teguran_list = [jenis_teguran_list] if jenis_teguran_list else []

                    jumlah_clue = raw_parsed.get("jumlah_clue_ingatan", 0) or 0

                    if jumlah_clue >= 7 and "Ingatan" not in jenis_teguran_list:
                        jenis_teguran_list.append("Ingatan")

                    if raw_parsed.get("catatan_koreksi") and "Tajwid/Makhraj" not in jenis_teguran_list:
                        jenis_teguran_list.append("Tajwid/Makhraj")

                    ada_teguran_final = len(jenis_teguran_list) > 0

                    final_draft = {
                        "surah_id": surah_info["surah_id"],
                        "surah_sahih": surah_info["surah_sahih"],
                        "ayat_standar": surah_info["ayat_standar"],
                        "status_kelancaran": raw_parsed.get("status_kelancaran", "Lancar"),
                        "catatan_koreksi": raw_parsed.get("catatan_koreksi"),
                        "jumlah_clue_ingatan": jumlah_clue,
                        "ada_teguran": ada_teguran_final,
                        "jenis_teguran": jenis_teguran_list,
                        "catatan_teguran": raw_parsed.get("catatan_teguran"),
                        "catatan_musyrif": raw_parsed.get("catatan_musyrif")
                    }

                    return {"status": "success", "draft": final_draft}

    except Exception as e:
        print(f"❌ VOICE PARSE ERROR: {e}")

    return {
        "status": "error_fallback",
        "message": "Gagal terhubung ke AI, silakan isi manual",
        "draft": {
            "surah_id": None,
            "surah_sahih": None,
            "ayat_standar": "-",
            "status_kelancaran": "Lancar",
            "catatan_koreksi": data.voice_command_text,
            "jumlah_clue_ingatan": 0,
            "ada_teguran": False,
            "jenis_teguran": [],
            "catatan_teguran": None,
            "catatan_musyrif": None
        }
    }

# ==========================================
# 3. POST SIMPAN SETORAN (Dengan Proteksi Status)
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

    target_santri_id = data.santri_id
    if not target_santri_id:
        raise HTTPException(status_code=422, detail="Field santri_id / student_id wajib diisi!")

    user = session.exec(select(User).where(User.username == username)).first()
    santri = session.exec(select(Santri).where(Santri.id == target_santri_id)).first()
    
    if not user or not santri:
        raise HTTPException(status_code=404, detail="User atau Santri tidak ditemukan")

    # 🛑 PROTEKSI STATUS: Hanya izinkan setoran jika santri berstatus 'hadir'
    if santri.status_santri != "hadir":
        status_info = {
            "izin": f"sedang IZIN ({santri.keterangan_izin or 'Tanpa Keterangan'})",
            "persiapan_ujian": f"sedang dalam masa PERSIAPAN UJIAN (Jadwal: {santri.tanggal_ujian or 'Belum diset'})",
            "remed_ujian": "sedang dalam masa REMEDIAL UJIAN"
        }.get(santri.status_santri, f"berstatus '{santri.status_santri}'")

        raise HTTPException(
            status_code=400, 
            detail=f"Tidak dapat menginput setoran! Santri {santri.nama_santri} {status_info}. Ubah status ke 'hadir' terlebih dahulu!"
        )

    final_surah = data.surah_sahih or data.surah or f"Surah ID: {data.surah_id}"
    final_ayat = data.ayat_standar or data.ayat or "-"
    
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
# 4. FITUR MUSYRIF: TARGET HARIAN, STATUS, & UJIAN
# ==========================================
@router.put("/santri/{santri_id}/target-harian")
def set_target_harian(
    santri_id: int, 
    payload: TargetMusyrifPayload, 
    session: Session = Depends(get_session)
):
    santri = session.get(Santri, santri_id)
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan!")

    santri.target_harian = payload.target_harian
    session.add(santri)
    session.commit()
    session.refresh(santri)
    
    return {
        "status": "success", 
        "message": f"Target harian untuk {santri.nama_santri} berhasil diperbarui!",
        "target_harian": santri.target_harian
    }


@router.put("/santri/{santri_id}/status")
def update_status_santri(
    santri_id: int, 
    payload: UpdateStatusPayload, 
    session: Session = Depends(get_session)
):
    status_valid = ["hadir", "izin", "persiapan_ujian", "remed_ujian"]
    if payload.status not in status_valid:
        raise HTTPException(status_code=400, detail=f"Status tidak valid! Pilih dari: {status_valid}")

    santri = session.get(Santri, santri_id)
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan!")

    santri.status_santri = payload.status
    if payload.status == "izin":
        santri.keterangan_izin = payload.keterangan or "Izin/Sakit"
    elif payload.status == "hadir":
        santri.keterangan_izin = None 

    log = StatusSantriLog(
        santri_id=santri.id,
        status=payload.status,
        keterangan=payload.keterangan
    )
    
    session.add(santri)
    session.add(log)
    session.commit()
    
    return {
        "status": "success",
        "message": f"Status {santri.nama_santri} berhasil diubah menjadi '{payload.status}'",
        "data": {
            "status_santri": santri.status_santri,
            "keterangan_izin": santri.keterangan_izin
        }
    }


@router.post("/santri/{santri_id}/daftar-ujian")
def daftarkan_ujian_santri(
    santri_id: int, 
    payload: DaftarUjianPayload, 
    session: Session = Depends(get_session)
):
    santri = session.get(Santri, santri_id)
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan!")

    santri.status_santri = "persiapan_ujian"
    santri.tanggal_ujian = payload.tanggal_ujian
    santri.catatan_persiapan_ujian = payload.catatan_persiapan

    log = StatusSantriLog(
        santri_id=santri.id,
        status="persiapan_ujian",
        keterangan=f"Didaftarkan ujian tanggal {payload.tanggal_ujian}. Catatan: {payload.catatan_persiapan or '-'}"
    )

    session.add(santri)
    session.add(log)
    session.commit()

    return {
        "status": "success",
        "message": f"Santri {santri.nama_santri} didaftarkan ujian tanggal {payload.tanggal_ujian}!",
        "status_santri": santri.status_santri
    }


@router.post("/santri/{santri_id}/hasil-ujian")
def submit_hasil_ujian(
    santri_id: int, 
    payload: HasilUjianPayload, 
    request: Request,
    session: Session = Depends(get_session),
    session_user: Optional[str] = Cookie(None)
):
    username = get_session_username(request, session_user)
    if not username:
        raise HTTPException(status_code=401, detail="Session expired / Belum login!")

    santri = session.get(Santri, santri_id)
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan")

    if santri.status_santri != "persiapan_ujian":
        raise HTTPException(status_code=400, detail="Santri tidak sedang dalam masa ujian!")

    # 🔄 Update status berdasarkan hasil
    if payload.hasil == "lulus":
        santri.status_santri = "hadir"
        pesan = f"Selamat! Santri {santri.nama_santri} LULUS ujian dan kembali ke halaqah."
    else:  # Jika remed
        santri.status_santri = "remed_ujian"
        pesan = f"Santri {santri.nama_santri} perlu REMEDIAL ujian. Tetap semangat!"

    # 📝 Catat ke Log Status
    catatan_teks = f"Hasil: {payload.hasil.upper()}."
    if payload.catatan:
        catatan_teks += f" Catatan Musyrif: {payload.catatan}"

    log_status = StatusSantriLog(
        santri_id=santri.id,
        status=santri.status_santri,
        keterangan=catatan_teks
    )
    
    session.add(santri)
    session.add(log_status)
    session.commit()

    return {
        "status": "success",
        "detail": pesan,
        "keterangan_log": catatan_teks
    }


# ==========================================
# 5. RANGKUMAN HARIAN & ANALISIS AI
# ==========================================
@router.post("/ai/rangkuman-harian/{santri_id}")
async def generate_rangkuman_harian(
    santri_id: int, 
    req: RangkumanHarianRequest, 
    session: Session = Depends(get_session)
):
    santri = session.get(Santri, santri_id)
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan")

    if req.tanggal:
        target_date = datetime.strptime(req.tanggal, "%Y-%m-%d").date()
    else:
        target_date = now_indonesia().date()

    start_of_day = datetime.combine(target_date, time.min, tzinfo=WIB)
    end_of_day = datetime.combine(target_date, time.max, tzinfo=WIB)

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
    - Catatan Evaluasi: [Poin kekurangan/kelebihan hari ini]
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


@router.post("/statistik/analisis/{santri_id}")
async def analyze_overall_santri(santri_id: int, session: Session = Depends(get_session)):
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
# 6. RAPORT SANTRI
# ==========================================
@router.post("/raport")
def submit_nilai_raport(data: RaportCreate, session: Session = Depends(get_session)):
    santri = session.get(Santri, data.santri_id)
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan")

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
    raports = session.exec(
        select(RaportSantri)
        .where(RaportSantri.santri_id == santri_id)
        .order_by(RaportSantri.id.desc())
    ).all()

    return {"status": "success", "data": raports}


# ==========================================
# 7. PROFILE DETAIL SANTRI (LENGKAP TARGET & UJIAN)
# ==========================================
@router.get("/santri/{santri_id}/profile")
def get_profile_santri(
    santri_id: int,
    session: Session = Depends(get_session)
):
    santri = session.get(Santri, santri_id)
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan!")

    kelompok = session.get(KelompokHalaqah, santri.kelompok_id) if santri.kelompok_id else None
    musyrif = session.get(User, kelompok.musyrif_id) if (kelompok and kelompok.musyrif_id) else None

    setoran_list = session.exec(
        select(SetoranTahfizh)
        .where(SetoranTahfizh.santri_id == santri_id)
        .order_by(SetoranTahfizh.id.desc())
    ).all()

    raport_list = session.exec(
        select(RaportSantri)
        .where(RaportSantri.santri_id == santri_id)
        .order_by(RaportSantri.id.desc())
    ).all()

    status_logs = session.exec(
        select(StatusSantriLog)
        .where(StatusSantriLog.santri_id == santri_id)
        .order_by(StatusSantriLog.id.desc())
    ).all()

    return {
        "status": "success",
        "profile": {
            "id": santri.id,
            "nama_santri": santri.nama_santri,
            "nomor_induk": getattr(santri, 'nomor_induk', '-'),
            "status_santri": getattr(santri, 'status_santri', 'hadir') or 'hadir',
            "keterangan_izin": getattr(santri, 'keterangan_izin', None),
            "target_semester": getattr(santri, 'target_semester', None),
            "target_harian": getattr(santri, 'target_harian', None),
            "tanggal_ujian": getattr(santri, 'tanggal_ujian', None),
            "catatan_persiapan_ujian": getattr(santri, 'catatan_persiapan_ujian', None),
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
        "raport": raport_list,
        "histori_status_log": [
            {
                "status": log.status,
                "keterangan": log.keterangan,
                "waktu": format_indonesia(log.created_at)
            } for log in status_logs
        ]
    }