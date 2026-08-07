import os
import csv
import json
import httpx
import re
import uuid
import shutil
import mimetypes
import base64
from typing import Optional, List, Literal, Dict, Any, Union
from datetime import datetime, date, time
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from rapidfuzz import process, fuzz
from passlib.context import CryptContext
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi import APIRouter, Depends, HTTPException, Request, Cookie, File, UploadFile
from sqlmodel import Session, select
from pydantic import BaseModel, Field, ConfigDict

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

app = FastAPI()

# Load Environment Variables
load_dotenv(override=True)  

# Konfigurasi OpenRouter AI
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = os.getenv("OPENROUTER_URL") or "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL") or "openrouter/free"

# Password Hashing Engine
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

router = APIRouter()
WIB = ZoneInfo("Asia/Jakarta")

# Path Penyimpanan Foto Profil
UPLOAD_DIR = "static/uploads/profiles"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Mount direktori static agar file upload bisa diakses publik
app.mount("/static", StaticFiles(directory="static"), name="static")

# ==========================================
# DYNAMIC RAPIDFUZZ DATASET & MATCHER ENGINE
# ==========================================
SURAH_MASTER_DATASET: Dict[int, Dict[str, Any]] = {}
SURAH_FUZZY_INDEX: Dict[str, int] = {}

def load_surah_dataset_from_csv(csv_path: str = "kamus_surah_bersih.csv"):
    global SURAH_MASTER_DATASET, SURAH_FUZZY_INDEX
    SURAH_MASTER_DATASET.clear()
    SURAH_FUZZY_INDEX.clear()

    if not os.path.exists(csv_path):
        print(f"⚠️ [WARNING] File CSV '{csv_path}' tidak ditemukan! Sistem berjalan dengan dataset surah kosong.")
        return

    try:
        with open(csv_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not row.get("id_surah"):
                    continue
                surah_id = int(row["id_surah"].strip())
                nama_surah = row.get("nama_surah", "").strip()
                
                nama_lain_list = [x.strip().lower() for x in row.get("nama_lain", "").split(",") if x.strip()]
                typo_list = [x.strip().lower() for x in row.get("typo_asr", "").split(",") if x.strip()]
                all_aliases = list(set(nama_lain_list + typo_list))

                SURAH_MASTER_DATASET[surah_id] = {
                    "nama": nama_surah,
                    "alias": all_aliases
                }

                if nama_surah:
                    SURAH_FUZZY_INDEX[nama_surah.lower()] = surah_id
                for alias in all_aliases:
                    SURAH_FUZZY_INDEX[alias] = surah_id

        print(f"✅ [INIT DATASET] Loaded {len(SURAH_MASTER_DATASET)} Surah & {len(SURAH_FUZZY_INDEX)} variasi typo/ASR ke RapidFuzz!")
    except Exception as e:
        print(f"❌ [ERROR LOAD CSV] Gagal membaca dataset surah: {e}")

# Inisialisasi Dataset Surah
load_surah_dataset_from_csv("kamus_surah_bersih.csv")


def find_best_surah_match(raw_query: str, cutoff_score: float = 65.0) -> Optional[Dict[str, Any]]:
    if not raw_query or not SURAH_FUZZY_INDEX:
        return None

    query_clean = raw_query.lower().strip()
    match = process.extractOne(
        query_clean,
        list(SURAH_FUZZY_INDEX.keys()),
        scorer=fuzz.WRatio,
        score_cutoff=cutoff_score
    )

    if match:
        matched_string, score, _ = match
        matched_surah_id = SURAH_FUZZY_INDEX[matched_string]
        surah_sahih = SURAH_MASTER_DATASET[matched_surah_id]["nama"]
        return {
            "surah_id": matched_surah_id,
            "surah_sahih": surah_sahih,
            "match_score": score
        }
    
    return None


# ==========================================
# SCHEMAS (PYDANTIC V2)
# ==========================================
class VoiceParseRequest(BaseModel):
    voice_command_text: Optional[str] = None
    raw_text: Optional[str] = None

    def get_text(self) -> str:
        return self.raw_text or self.voice_command_text or ""

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
    jenis_teguran: Optional[Union[List[str], str]] = None
    catatan_teguran: Optional[str] = None
    catatan_musyrif: Optional[str] = ""

    model_config = ConfigDict(populate_by_name=True)

class TargetMusyrifPayload(BaseModel):
    target_harian: str

class UpdateStatusPayload(BaseModel):
    status: str
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
    santri_id: int = Field(alias="student_id")
    semester: str
    nilai_harian: float
    nilai_bulanan: float
    nilai_akhir: float
    catatan_musyrif: Optional[str] = ""
    rekomendasi_ai: Optional[str] = None
    auto_generate_ai: Optional[bool] = True

    model_config = ConfigDict(populate_by_name=True)

class RaportPreviewRequest(BaseModel):
    santri_id: int = Field(alias="student_id")
    semester: str
    nilai_harian: float
    nilai_bulanan: float
    nilai_akhir: float
    catatan_musyrif: Optional[str] = ""

    model_config = ConfigDict(populate_by_name=True)

class UpdateMusyrifProfilePayload(BaseModel):
    nama_lengkap: Optional[str] = None
    username: Optional[str] = None

class ChangePasswordPayload(BaseModel):
    password_lama: str
    password_baru: str
    konfirmasi_password_baru: str

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def get_session_username(request: Request, session_user: Optional[str] = None) -> Optional[str]:
    return session_user or request.headers.get("x-session-user") or request.cookies.get("x-session-user")


def get_quran_mapping_from_db(
    session: Session, 
    juz: Optional[int], 
    kaca: Optional[int], 
    bagian: Optional[str] = None
) -> dict:
    if not juz or not kaca:
        return {"surah_id": None, "surah_sahih": None, "ayat_standar": "-"}

    mapping = session.exec(
        select(QuranPage)
        .where(QuranPage.juz == juz)
        .where(QuranPage.kaca == kaca)
    ).first()

    if not mapping:
        return {"surah_id": None, "surah_sahih": None, "ayat_standar": "-"}

    a_start, a_end = mapping.ayat_start, mapping.ayat_end

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


def verify_password_check(plain_password: str, hashed_or_plain: str) -> bool:
    if not hashed_or_plain:
        return False
    if hashed_or_plain.startswith(("$2b$", "$2a$", "$pbkdf2$", "$argon2$")):
        try:
            return pwd_context.verify(plain_password, hashed_or_plain)
        except Exception:
            return False
    return plain_password == hashed_or_plain


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


# ==========================================
# 1. GET DAFTAR SANTRI
# ==========================================
@router.get("/halaqah")
@router.get("/santri")
def get_all_santri(
    request: Request,
    session: Session = Depends(get_session),
    session_user: Optional[str] = Cookie(None)
):
    username = get_session_username(request, session_user)
    if not username:
        raise HTTPException(status_code=401, detail="Belum login / Session expired!")
    
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
            "target_harian": getattr(s, "target_harian", None),
            "foto_profile": getattr(s, "foto_profile", None)
        } for s in santri_list
    ]

    return {
        "status": "success",
        "nama_ustadz": nama_ustadz,
        "data": formatted_santri,
        "santri": formatted_santri
    }


# ==========================================
# 2. VOICE PARSING (HYBRID LLM + RAPIDFUZZ)
# ==========================================
@router.post("/parse-voice")
async def parse_voice_command(
    data: VoiceParseRequest, 
    session: Session = Depends(get_session)
):
    command_text = data.get_text()

    if not command_text.strip():
        raise HTTPException(status_code=422, detail="Teks transkrip suara kosong!")

    system_prompt = """
Kamu adalah AI Asisten Musyrif Tahfizh.
Tugasmu MENGOREKSI kesalahan dengar (typo) STT Browser dan mengekstrak data setoran sekaligus evaluasi santri secara BERSAMAAN dari ucapan Musyrif.

ATURAN PARSING:
- status_kelancaran: Pilih ["Lancar", "Cukup Lancar", "Kurang Lancar", "Mengulang"]. Default: "Lancar".
- jenis_teguran: Array HANYA berisi ["Tajwid/Makhraj", "Ingatan", "Adab", "Kedisiplinan", "Lainnya"].

FORMAT JSON:
{
    "juz": null,
    "kaca": null,
    "bagian": null,
    "surah_langsung": null,
    "ayat_langsung": null,
    "status_kelancaran": "Lancar",
    "catatan_koreksi": null,
    "jumlah_clue_ingatan": 0,
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
            {"role": "user", "content": f"Ucapan Musyrif: \"{command_text}\""}
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
                    
                    content_clean = content.strip()
                    if content_clean.startswith("```json"): content_clean = content_clean[7:]
                    if content_clean.startswith("```"): content_clean = content_clean[3:]
                    if content_clean.endswith("```"): content_clean = content_clean[:-3]
                    
                    raw_parsed = json.loads(content_clean.strip())

                    raw_surah = raw_parsed.get("surah_langsung")
                    raw_kaca = raw_parsed.get("kaca")
                    bagian_val = str(raw_parsed.get("bagian")).lower() if raw_parsed.get("bagian") else None
                    kaca_clean = None

                    if raw_kaca is not None:
                        match = re.search(r'(\d+)\s*([a-bA-B]?)', str(raw_kaca))
                        if match:
                            kaca_clean = int(match.group(1))
                            if match.group(2) and not bagian_val:
                                bagian_val = match.group(2).lower()

                    surah_info = None

                    if raw_surah:
                        fuzzy_match = find_best_surah_match(raw_surah)
                        if fuzzy_match:
                            surah_info = {
                                "surah_id": fuzzy_match["surah_id"],
                                "surah_sahih": fuzzy_match["surah_sahih"],
                                "ayat_standar": raw_parsed.get("ayat_langsung") or "-"
                            }
                        else:
                            surah_info = {
                                "surah_id": None,
                                "surah_sahih": raw_surah,
                                "ayat_standar": raw_parsed.get("ayat_langsung") or "-"
                            }

                    if not surah_info or not surah_info.get("surah_id"):
                        if raw_parsed.get("juz") or kaca_clean:
                            db_mapping = get_quran_mapping_from_db(
                                session=session,
                                juz=raw_parsed.get("juz"),
                                kaca=kaca_clean,
                                bagian=bagian_val
                            )
                            if db_mapping.get("surah_id"):
                                surah_info = db_mapping

                    if not surah_info or not surah_info.get("surah_id"):
                        fallback_match = find_best_surah_match(command_text, cutoff_score=75.0)
                        if fallback_match:
                            surah_info = {
                                "surah_id": fallback_match["surah_id"],
                                "surah_sahih": fallback_match["surah_sahih"],
                                "ayat_standar": raw_parsed.get("ayat_langsung") or "-"
                            }
                        else:
                            surah_info = surah_info or {"surah_id": None, "surah_sahih": None, "ayat_standar": "-"}

                    jenis_teguran_list = raw_parsed.get("jenis_teguran", [])
                    if isinstance(jenis_teguran_list, str):
                        jenis_teguran_list = [jenis_teguran_list] if jenis_teguran_list else []

                    jumlah_clue = raw_parsed.get("jumlah_clue_ingatan", 0) or 0

                    if jumlah_clue >= 7 and "Ingatan" not in jenis_teguran_list:
                        jenis_teguran_list.append("Ingatan")
                    if raw_parsed.get("catatan_koreksi") and "Tajwid/Makhraj" not in jenis_teguran_list:
                        jenis_teguran_list.append("Tajwid/Makhraj")

                    ada_teguran_final = len(jenis_teguran_list) > 0
                    jenis_teguran_rapih = ", ".join(list(set(jenis_teguran_list))) if ada_teguran_final else ""

                    final_draft = {
                        **surah_info,
                        "status_kelancaran": raw_parsed.get("status_kelancaran", "Lancar"),
                        "catatan_koreksi": raw_parsed.get("catatan_koreksi"),
                        "jumlah_clue_ingatan": jumlah_clue,
                        "ada_teguran": ada_teguran_final,
                        "jenis_teguran": jenis_teguran_rapih,
                        "catatan_teguran": raw_parsed.get("catatan_teguran"),
                        "catatan_musyrif": raw_parsed.get("catatan_musyrif")
                    }
                    
                    return {"status": "success", "mode": "combined", "draft": final_draft}

    except Exception as e:
        print(f"❌ VOICE PARSE ERROR: {e}")

    fallback_surah = find_best_surah_match(command_text, cutoff_score=60.0)
    return {
        "status": "error_fallback",
        "message": "AI Parser offline/error, menggunakan fallback RapidFuzz",
        "draft": {
            "surah_id": fallback_surah["surah_id"] if fallback_surah else None,
            "surah_sahih": fallback_surah["surah_sahih"] if fallback_surah else None,
            "ayat_standar": "-",
            "status_kelancaran": "Lancar"
        }
    }


# ==========================================
# 3. POST SIMPAN SETORAN
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
        raise HTTPException(status_code=422, detail="Field student_id / santri_id wajib diisi!")

    user = session.exec(select(User).where(User.username == username)).first()
    santri = session.exec(select(Santri).where(Santri.id == target_santri_id)).first()
    
    if not user or not santri:
        raise HTTPException(status_code=404, detail="User atau Santri tidak ditemukan")

    if santri.status_santri != "hadir":
        status_info = {
            "izin": f"sedang IZIN ({santri.keterangan_izin or 'Tanpa Keterangan'})",
            "persiapan_ujian": f"sedang PERSIAPAN UJIAN (Jadwal: {santri.tanggal_ujian or 'Belum diset'})",
            "remed_ujian": "sedang REMEDIAL UJIAN"
        }.get(santri.status_santri, f"berstatus '{santri.status_santri}'")

        raise HTTPException(
            status_code=400, 
            detail=f"Tidak dapat menginput setoran! Santri {santri.nama_santri} {status_info}. Ubah status ke 'hadir' terlebih dahulu!"
        )

    final_surah = data.surah_sahih or data.surah or (f"Surah ID: {data.surah_id}" if data.surah_id else "Surah Tidak Terdefinisi")
    final_ayat = data.ayat_standar or data.ayat or "-"
    
    notes = []
    if data.catatan_koreksi:
        notes.append(f"Koreksi: {data.catatan_koreksi}")
    
    if data.ada_teguran:
        if isinstance(data.jenis_teguran, list):
            kategori_teks = ", ".join(data.jenis_teguran) if data.jenis_teguran else "Adab Umum"
        else:
            kategori_teks = data.jenis_teguran if data.jenis_teguran else "Adab Umum"

        catatan_teg = data.catatan_teguran or "-"
        notes.append(f"Teguran ({kategori_teks}): {catatan_teg}")
        
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

    if payload.hasil == "lulus":
        santri.status_santri = "hadir"
        pesan = f"Selamat! Santri {santri.nama_santri} LULUS ujian dan kembali ke halaqah."
    else:
        santri.status_santri = "remed_ujian"
        pesan = f"Santri {santri.nama_santri} perlu REMEDIAL ujian. Tetap semangat!"

    santri.tanggal_ujian = None
    santri.catatan_persiapan_ujian = None

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
# 5. STATISTIK & HISTORI SETORAN SANTRI
# ==========================================
@router.get("/statistik/setoran/{santri_id}")
def get_statistik_setoran_santri(santri_id: int, session: Session = Depends(get_session)):
    santri = session.get(Santri, santri_id)
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan!")

    setoran_list = session.exec(
        select(SetoranTahfizh)
        .where(SetoranTahfizh.santri_id == santri_id)
        .order_by(SetoranTahfizh.id.desc())
    ).all()

    return {
        "status": "success",
        "santri_id": santri_id,
        "nama_santri": santri.nama_santri,
        "total_setoran": len(setoran_list),
        "riwayat_lengkap": [
            {
                "id": s.id,
                "surah": s.surah,
                "ayat": s.ayat,
                "status": s.status_kelancaran.lower() if s.status_kelancaran else "lancar",
                "status_kelancaran": s.status_kelancaran or "Lancar",
                "catatan": getattr(s, "catatan_musyrif", ""),
                "waktu": format_indonesia(getattr(s, "created_at", None))
            } for s in setoran_list
        ]
    }


# ==========================================
# 6. ANALISA AI HARIAN SANTRI
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

    try:
        if req.tanggal:
            target_date = datetime.strptime(req.tanggal, "%Y-%m-%d").date()
        else:
            target_date = now_indonesia().date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal tidak valid! Gunakan format YYYY-MM-DD.")

    start_of_day = datetime.combine(target_date, time.min)
    end_of_day = datetime.combine(target_date, time.max)

    setoran_today = session.exec(
        select(SetoranTahfizh)
        .where(SetoranTahfizh.santri_id == santri_id)
        .where(SetoranTahfizh.created_at >= start_of_day)
        .where(SetoranTahfizh.created_at <= end_of_day)
    ).all()

    status_log_today = session.exec(
        select(StatusSantriLog)
        .where(StatusSantriLog.santri_id == santri_id)
        .where(StatusSantriLog.created_at >= start_of_day)
        .where(StatusSantriLog.created_at <= end_of_day)
        .order_by(StatusSantriLog.id.desc())
    ).first()

    status_absensi = santri.status_santri
    keterangan_absensi = santri.keterangan_izin or "Hadir di halaqah"
    if status_log_today:
        status_absensi = status_log_today.status
        if status_log_today.keterangan:
            keterangan_absensi = status_log_today.keterangan

    if not setoran_today and status_absensi != "hadir":
        return {
            "status": "success",
            "tanggal": target_date.strftime("%Y-%m-%d"),
            "rangkuman_ai": f"Santri {santri.nama_santri} berhalangan setoran pada {target_date.strftime('%d %B %Y')}. Status Absensi: {status_absensi.upper()} ({keterangan_absensi})."
        }

    catatan_setoran_teks = "\n".join([
        f"- Surah {s.surah}:{s.ayat} | Status: {s.status_kelancaran} | Catatan Evaluasi/Koreksi: {s.catatan_musyrif or 'Tidak ada catatan'}"
        for s in setoran_today
    ]) if setoran_today else "Belum/Tidak ada setoran hafalan yang dicatat hari ini."

    target_harian = getattr(santri, 'target_harian', 'Belum diset')

    prompt = f"""
    Bertindaklah sebagai AI Evaluator Musyrif Tahfizh Harian.
    Tugas: Analisis sesi halaqah harian santri berdasarkan parameter kualitas bacaan, konsistensi setoran, dan absensi.

    INFORMASI SANTRI & HARIAN:
    - Nama Santri: {santri.nama_santri}
    - Tanggal Evaluasi: {target_date.strftime('%d %B %Y')}
    - Status Absensi: {status_absensi.upper()} ({keterangan_absensi})
    - Target Harian Santri: {target_harian}
    - Jumlah Setoran Hari Ini: {len(setoran_today)} kali

    DETAIL SETORAN HARI INI:
    {catatan_setoran_teks}

    KRITERIA EVALUASI YANG WAJIB DIANALISIS:
    1. Kualitas Bacaan Setoran: Evaluasi secara khusus aspek Tajwid, Kelancaran, Makhraj, dan Panjang-Pendek berdasarkan data setoran.
    2. Konsistensi Setoran Halaqah: Evaluasi sejauh mana konsistensi santri menyetorkan hafalannya hari ini dibandingkan target harian.
    3. Kehadiran & Absensi: Catat ringkasan kehadiran/keikutsertaan santri di halaqah.

    FORMAT JAWABAN (Singkat, Padat, Solutif):
    📌 **Absensi & Kehadiran**: [Ringkasan status absensi santri hari ini]
    📖 **Evaluasi Kualitas Setoran**:
    - Tajwid & Makhraj: [Evaluasi]
    - Kelancaran & Panjang-Pendek: [Evaluasi]
    ⚡ **Konsistensi Halaqah**: [Tingkat konsistensi setoran dibanding target harian]
    💡 **Rekomendasi Musyrif**: [1 kalimat saran singkat untuk halaqah esok hari]
    """

    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": "Kamu adalah AI Evaluator Tahfizh Harian yang cermat dan berfokus pada tajwid, makhraj, kelancaran, panjang-pendek, serta konsistensi halaqah."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2
    }

    rangkuman = "Gagal memproses analisa harian."
    try:
        if OPENROUTER_API_KEY:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
                if resp.status_code == 200:
                    rangkuman = resp.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        rangkuman = f"Error koneksi AI: {e}"

    return {
        "status": "success",
        "tanggal": target_date.strftime("%Y-%m-%d"),
        "total_setoran_hari_ini": len(setoran_today),
        "status_absensi": status_absensi,
        "rangkuman_ai": rangkuman
    }


# ==========================================
# 7. RAPORT SANTRI & COPILOT AI RAPORT
# ==========================================
@router.post("/raport/preview-ai")
async def preview_ai_raport_copilot(
    data: RaportPreviewRequest,
    session: Session = Depends(get_session)
):
    santri = session.get(Santri, data.santri_id)
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan!")

    setoran_list = session.exec(
        select(SetoranTahfizh)
        .where(SetoranTahfizh.santri_id == data.santri_id)
        .order_by(SetoranTahfizh.id.asc())
    ).all()

    logs_absensi = session.exec(
        select(StatusSantriLog)
        .where(StatusSantriLog.santri_id == data.santri_id)
        .order_by(StatusSantriLog.id.asc())
    ).all()

    setoran_teks = "\n".join([
        f"- Surah {s.surah}:{s.ayat} ({s.status_kelancaran}) | Catatan: {s.catatan_musyrif or '-'}"
        for s in setoran_list
    ]) if setoran_list else "Belum ada histori setoran."

    rekap_absensi_teks = ", ".join([f"{l.status}" for l in logs_absensi]) if logs_absensi else "Tidak ada riwayat ketidakhadiran khusus."

    prompt_copilot = f"""
    Tugas: Bertindaklah sebagai Copilot AI Evaluator Raport Tahfizh. 
    Buat analisa perkembangan kumulatif santri berdasarkan input Nilai Raport, Catatan Musyrif, Kualitas Setoran (Tajwid, Kelancaran, Makhraj, Panjang Pendek), Konsistensi Halaqah, dan Absensi.

    INPUT RAPORT & KINERJA SANTRI:
    - Nama Santri: {santri.nama_santri}
    - Semester: {data.semester}
    - Nilai Harian (NH): {data.nilai_harian}
    - Nilai Bulanan (NB): {data.nilai_bulanan}
    - Nilai Akhir (NA): {data.nilai_akhir}
    - Catatan dari Musyrif: {data.catatan_musyrif or 'Tidak ada catatan tambahan'}

    HISTORI SELURUH SETORAN TAHFIZH SANTRI ({len(setoran_list)} setoran):
    {setoran_teks}

    REKAP ABSENSI / STATUS LOG KESELURUHAN:
    {rekap_absensi_teks}

    FORMAT ANALISA RAPORT AI (Ringkas & Mengedukasi, Maks 150 kata):
    📊 **Analisa Akademik & Kinerja**:
    [Review kombinasi NH={data.nilai_harian}, NB={data.nilai_bulanan}, NA={data.nilai_akhir} dengan catatan musyrif]

    📖 **Evaluasi Kualitas Setoran & Konsistensi**:
    - Kualitas Bacaan (Tajwid, Makhraj, Kelancaran, Panjang-Pendek): [Penilaian kumulatif]
    - Kedisiplinan Absensi & Konsistensi Halaqah: [Penilaian konsistensi setoran]

    💡 **Rekomendasi AI**:
    1. [Rekomendasi pengembangan untuk santri]
    2. [Pesan pendampingan untuk orang tua santri]
    """

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": "Kamu adalah AI Evaluator Raport Tahfizh yang solutif, objektif, dan memperhatikan detail tajwid, makhraj, kelancaran, serta absensi."},
            {"role": "user", "content": prompt_copilot}
        ],
        "temperature": 0.3,
        "max_tokens": 500
    }

    rekomendasi_generated = "Gagal memproses rekomendasi AI."
    try:
        if OPENROUTER_API_KEY:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
                if resp.status_code == 200:
                    res_json = resp.json()
                    rekomendasi_generated = res_json['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f"❌ [COPILOT AI PREVIEW ERROR]: {e}")
        rekomendasi_generated = f"Terjadi kesalahan koneksi AI: {e}"

    return {
        "status": "success",
        "santri_id": data.santri_id,
        "rekomendasi_ai": rekomendasi_generated
    }


@router.post("/raport")
async def submit_nilai_raport(data: RaportCreate, session: Session = Depends(get_session)):
    santri = session.get(Santri, data.santri_id)
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan")

    final_rekomendasi_ai = data.rekomendasi_ai

    if not final_rekomendasi_ai and data.auto_generate_ai:
        setoran_list = session.exec(
            select(SetoranTahfizh)
            .where(SetoranTahfizh.santri_id == data.santri_id)
            .order_by(SetoranTahfizh.id.asc())
        ).all()

        logs_absensi = session.exec(
            select(StatusSantriLog)
            .where(StatusSantriLog.santri_id == data.santri_id)
            .order_by(StatusSantriLog.id.asc())
        ).all()

        setoran_teks = "\n".join([
            f"- Surah {s.surah}:{s.ayat} ({s.status_kelancaran}) | Catatan: {s.catatan_musyrif or '-'}"
            for s in setoran_list
        ]) if setoran_list else "Belum ada histori setoran."

        rekap_absensi_teks = ", ".join([f"{l.status}" for l in logs_absensi]) if logs_absensi else "Hadir konsisten"

        prompt_raport = f"""
        Tugas: Buat Kesimpulan, Analisa Perkembangan, dan Rekomendasi AI untuk Raport Santri secara padat dan terstruktur.

        INPUT DATA RAPORT:
        - Nama Santri: {santri.nama_santri}
        - Semester: {data.semester}
        - Nilai Harian (NH): {data.nilai_harian}
        - Nilai Bulanan (NB): {data.nilai_bulanan}
        - Nilai Akhir (NA): {data.nilai_akhir}
        - Catatan Musyrif: {data.catatan_musyrif or 'Tidak ada catatan'}

        HISTORI SELURUH SETORAN TAHFIZH SANTRI ({len(setoran_list)} setoran):
        {setoran_teks}

        REKAP KEHADIRAN / ABSENSI:
        {rekap_absensi_teks}

        FORMAT OUTPUT:
        📊 **Kesimpulan & Analisa Raport**:
        [Analisa performa akademis (NH/NB/NA), kesesuaian catatan musyrif, dan komitmen santri]

        📖 **Kualitas Bacaan & Konsistensi Halaqah**:
        [Evaluasi Tajwid, Makhraj, Kelancaran, Panjang-Pendek, serta tingkat konsistensi setoran saat halaqah & absensi]

        💡 **Rekomendasi AI**:
        1. [Langkah perbaikan hafalan/murojaah ke depan]
        2. [Rekomendasi untuk perbaikan kualitas/konsistensi]
        """

        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": OPENROUTER_MODEL,
            "messages": [
                {"role": "system", "content": "Kamu adalah AI Evaluator Raport Tahfizh yang objektif, mendukung, dan mendetail."},
                {"role": "user", "content": prompt_raport}
            ],
            "temperature": 0.3
        }

        try:
            if OPENROUTER_API_KEY:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
                    if resp.status_code == 200:
                        res_json = resp.json()
                        final_rekomendasi_ai = res_json['choices'][0]['message']['content'].strip()
        except Exception as e:
            print(f"❌ [AI RAPORT ERROR]: {e}")
            final_rekomendasi_ai = "Gagal memproses analisa AI otomatis."

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
        existing_raport.rekomendasi_ai = final_rekomendasi_ai
        
        session.add(existing_raport)
        session.commit()
        session.refresh(existing_raport)
        return {"status": "success", "message": "Nilai Raport & Analisa AI berhasil diperbarui!", "data": existing_raport}

    new_raport = RaportSantri(
        santri_id=data.santri_id,
        semester=data.semester,
        nilai_harian=data.nilai_harian,
        nilai_bulanan=data.nilai_bulanan,
        nilai_akhir=data.nilai_akhir,
        catatan_musyrif=data.catatan_musyrif,
        rekomendasi_ai=final_rekomendasi_ai
    )
    session.add(new_raport)
    session.commit()
    session.refresh(new_raport)

    return {"status": "success", "message": "Nilai Raport & Analisa AI berhasil disimpan!", "data": new_raport}


@router.get("/raport/{santri_id}")
def get_nilai_raport(santri_id: int, session: Session = Depends(get_session)):
    raports = session.exec(
        select(RaportSantri)
        .where(RaportSantri.santri_id == santri_id)
        .order_by(RaportSantri.id.desc())
    ).all()

    return {"status": "success", "data": raports}


# ==========================================
# 8. PROFILE DETAIL SANTRI
# ==========================================
@router.get("/santri/{santri_id}/profile")
def get_profile_santri(
    santri_id: int,
    session: Session = Depends(get_session)
):
    santri = session.get(Santri, santri_id)
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan!")

    kelompok = session.get(KelompokHalaqah, santri.kelompok_id) if getattr(santri, 'kelompok_id', None) else None
    musyrif = session.get(User, kelompok.musyrif_id) if (kelompok and getattr(kelompok, 'musyrif_id', None)) else None

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
            "kelompok_id": getattr(santri, 'kelompok_id', None),
            "nama_kelompok": kelompok.nama_kelompok if kelompok else "Belum Masuk Kelompok",
            "musyrif_id": musyrif.id if musyrif else None,
            "nama_musyrif": getattr(musyrif, 'nama_lengkap', musyrif.username) if musyrif else "Belum Diplotting",
            "foto_profile": getattr(santri, 'foto_profile', None),
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
                "waktu": format_indonesia(getattr(log, 'created_at', None))
            } for log in status_logs
        ]
    }


@router.post("/santri/{santri_id}/upload-foto")
async def upload_foto_santri(
    santri_id: int,
    request: Request,
    file: Optional[UploadFile] = File(None),
    foto: Optional[UploadFile] = File(None),
    session: Session = Depends(get_session),
    session_user: Optional[str] = Cookie(None)
):
    username = get_session_username(request, session_user)
    if not username:
        raise HTTPException(status_code=401, detail="Session expired")

    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")

    santri = session.get(Santri, santri_id)
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan")

    if user.role == "musyrif":
        kelompok = session.get(KelompokHalaqah, santri.kelompok_id) if getattr(santri, "kelompok_id", None) else None
        if not kelompok or kelompok.musyrif_id != user.id:
            raise HTTPException(status_code=403, detail="Anda tidak berwenang mengubah foto santri ini.")
    elif user.role != "admin":
        raise HTTPException(status_code=403, detail="Role tidak diizinkan!")

    target_file = file or foto
    if not target_file:
        try:
            form = await request.form()
            for key, val in form.items():
                candidate = val[0] if isinstance(val, list) and val else val
                if hasattr(candidate, "filename") and getattr(candidate, "filename"):
                    target_file = candidate
                    break
        except Exception as e:
            print(f"[DEBUG upload_foto_santri] Error parsing form: {e}")

    if not target_file or not getattr(target_file, "filename", None):
        raise HTTPException(status_code=400, detail="File gambar wajib diunggah! Kirim form-data dengan key 'file' atau 'foto'.")

    content_type = getattr(target_file, "content_type", "")
    if content_type and not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File yang diunggah harus berupa gambar (JPG/PNG/WEBP)!")

    ext = os.path.splitext(target_file.filename)[1] or ".jpg"
    filename = f"santri_{santri.id}_{uuid.uuid4().hex[:8]}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    contents = await target_file.read()
    with open(filepath, "wb") as buffer:
        buffer.write(contents)

    file_url = f"/static/uploads/profiles/{filename}"
    santri.foto_profile = file_url

    session.add(santri)
    session.commit()
    session.refresh(santri)

    return {"status": "success", "foto_profile": file_url}


# ==========================================
# 9. PROFILE MUSYRIF / USER LOGGED IN
# ==========================================
@router.get("/profile")
def get_musyrif_profile(
    request: Request,
    session: Session = Depends(get_session),
    session_user: Optional[str] = Cookie(None)
):
    username = get_session_username(request, session_user)
    if not username:
        raise HTTPException(status_code=401, detail="Session expired")

    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")

    return {
        "status": "success",
        "data": {
            "id": user.id,
            "nama_lengkap": getattr(user, "nama_lengkap", user.username),
            "username": user.username,
            "foto_profile": getattr(user, "foto_profile", None),
            "tanggal_bikin_akun": getattr(user, "created_at", None)
        }
    }


@router.put("/profile")
def update_musyrif_profile(
    data: UpdateMusyrifProfilePayload,
    request: Request,
    session: Session = Depends(get_session),
    session_user: Optional[str] = Cookie(None)
):
    username = get_session_username(request, session_user)
    if not username:
        raise HTTPException(status_code=401, detail="Session expired")

    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")

    if data.nama_lengkap:
        user.nama_lengkap = data.nama_lengkap

    if data.username and data.username != user.username:
        existing = session.exec(select(User).where(User.username == data.username)).first()
        if existing:
            raise HTTPException(status_code=400, detail="Username sudah digunakan!")
        user.username = data.username

    session.add(user)
    session.commit()
    session.refresh(user)

    return {"status": "success", "message": "Profil berhasil diperbarui"}


@router.post("/profile/upload-foto")
async def upload_foto_profile(
    request: Request,
    file: Optional[UploadFile] = File(None),
    foto: Optional[UploadFile] = File(None),
    session: Session = Depends(get_session),
    session_user: Optional[str] = Cookie(None)
):
    username = get_session_username(request, session_user)
    if not username:
        raise HTTPException(status_code=401, detail="Session expired")

    target_file = file or foto

    if not target_file:
        try:
            form = await request.form()
            for key, val in form.items():
                candidate = val[0] if isinstance(val, list) and val else val
                if candidate is None:
                    continue

                if hasattr(candidate, "filename") and getattr(candidate, "filename"):
                    target_file = candidate
                    break
        except Exception as e:
            print(f"[DEBUG upload_foto_profile] Error parsing form: {e}")

    # Fallback: accept raw image body or JSON containing base64 image
    if not target_file:
        content_type_header = (request.headers.get('content-type') or '').lower()
        try:
            raw_body = await request.body()
        except Exception as e:
            raw_body = b""

        if raw_body:
            if 'application/json' in content_type_header:
                try:
                    payload = json.loads(raw_body.decode('utf-8'))
                    b64_data = None
                    for k in ('file', 'foto', 'image', 'image_base64', 'data'):
                        if k in payload and isinstance(payload[k], str):
                            b64_data = payload[k]
                            break

                    if not b64_data and 'file' in payload and isinstance(payload['file'], dict):
                        file_obj = payload['file']
                        for fk, fv in file_obj.items():
                            if isinstance(fv, str):
                                if 'base64' in fv or 'data:' in fv:
                                    b64_data = fv.split(',', 1)[1] if 'base64,' in fv else fv
                                    break
                                if len(fv) > 100 and re.fullmatch(r"[A-Za-z0-9+/=\n\r]+", fv):
                                    b64_data = fv
                                    break
                        if not b64_data:
                            raise HTTPException(
                                status_code=400,
                                detail="Payload 'file' berupa objek tetapi tidak berisi base64 gambar yang valid."
                            )

                    if b64_data:
                        if b64_data.startswith('data:'):
                            b64_data = b64_data.split(',', 1)[1]
                        img_bytes = base64.b64decode(b64_data)
                        user = session.exec(select(User).where(User.username == username)).first()
                        if not user:
                            raise HTTPException(status_code=404, detail="User tidak ditemukan")
                        ext = '.jpg'
                        filename = f"profile_{user.id}_{uuid.uuid4().hex[:8]}{ext}"
                        filepath = os.path.join(UPLOAD_DIR, filename)
                        with open(filepath, 'wb') as f:
                            f.write(img_bytes)
                        file_url = f"/static/uploads/profiles/{filename}"
                        user.foto_profile = file_url
                        session.add(user)
                        session.commit()
                        session.refresh(user)
                        return {"status": "success", "foto_profile": file_url}
                except Exception as e:
                    print(f"[DEBUG upload_foto_profile] JSON/base64 fallback failed: {e}")

            if content_type_header.startswith('image/') or 'application/octet-stream' in content_type_header:
                try:
                    user = session.exec(select(User).where(User.username == username)).first()
                    if not user:
                        raise HTTPException(status_code=404, detail="User tidak ditemukan")
                    mime = content_type_header.split(';')[0]
                    ext = mimetypes.guess_extension(mime) or '.jpg'
                    filename = f"profile_{user.id}_{uuid.uuid4().hex[:8]}{ext}"
                    filepath = os.path.join(UPLOAD_DIR, filename)
                    with open(filepath, 'wb') as f:
                        f.write(raw_body)
                    file_url = f"/static/uploads/profiles/{filename}"
                    user.foto_profile = file_url
                    session.add(user)
                    session.commit()
                    session.refresh(user)
                    return {"status": "success", "foto_profile": file_url}
                except HTTPException:
                    raise
                except Exception as e:
                    print(f"[DEBUG upload_foto_profile] raw-body fallback failed: {e}")

    if not target_file or not getattr(target_file, "filename", None):
        raise HTTPException(
            status_code=400, 
            detail="File gambar wajib diunggah! Kirim form-data dengan key 'file' atau 'foto'."
        )

    content_type = getattr(target_file, "content_type", "")
    if content_type and not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File yang diunggah harus berupa gambar (JPG/PNG/WEBP)!")

    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")

    ext = os.path.splitext(target_file.filename)[1] or ".jpg"
    filename = f"profile_{user.id}_{uuid.uuid4().hex[:8]}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    contents = await target_file.read()
    with open(filepath, "wb") as buffer:
        buffer.write(contents)

    file_url = f"/static/uploads/profiles/{filename}"
    user.foto_profile = file_url

    session.add(user)
    session.commit()
    session.refresh(user)

    return {"status": "success", "foto_profile": file_url}


@router.put("/profile/ganti-password")
def ganti_password_musyrif(
    data: ChangePasswordPayload,
    request: Request,
    session: Session = Depends(get_session),
    session_user: Optional[str] = Cookie(None)
):
    username = get_session_username(request, session_user)
    if not username:
        raise HTTPException(status_code=401, detail="Session expired")

    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")

    current_pwd = getattr(user, "password_hash", None) or getattr(user, "hashed_password", None) or getattr(user, "password", None)

    if not current_pwd:
        raise HTTPException(status_code=500, detail="Field password tidak ditemukan pada model User!")

    if not verify_password_check(data.password_lama, current_pwd):
        raise HTTPException(status_code=400, detail="Password lama salah!")

    if data.password_baru != data.konfirmasi_password_baru:
        raise HTTPException(status_code=400, detail="Konfirmasi password baru tidak sesuai!")

    hashed_new_pwd = get_password_hash(data.password_baru)

    if hasattr(user, "password_hash"):
        user.password_hash = hashed_new_pwd
    elif hasattr(user, "hashed_password"):
        user.hashed_password = hashed_new_pwd
    elif hasattr(user, "password"):
        user.password = hashed_new_pwd

    session.add(user)
    session.commit()

    return {"status": "success", "message": "Password berhasil diubah"}