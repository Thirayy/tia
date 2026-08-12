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
from datetime import datetime, date, time, timedelta
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


def find_best_surah_match(raw_query: str, cutoff_score: float = 60.0) -> Optional[Dict[str, Any]]:
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

class TajwidDetailSchema(BaseModel):
    rule: str
    error_description: str
    count: int

class MakhrajDetailSchema(BaseModel):
    letter: str
    error_description: str
    count: int

class SetoranCreate(BaseModel):
    santri_id: Optional[int] = Field(default=None, alias="student_id")
    surah_id: Optional[int] = None
    surah_sahih: Optional[str] = None
    surah: Optional[str] = None
    ayat_standar: Optional[str] = None
    ayat: Optional[str] = None
    status_kelancaran: Literal["lancar", "tersendat", "gagal", "ulang"] = "lancar"
    catatan_musyrif: Optional[str] = ""
    jumlah_tersendat: int = 0
    jumlah_teguran: int = 0
    tajwid_details: Optional[List[TajwidDetailSchema]] = []
    makhraj_details: Optional[List[MakhrajDetailSchema]] = []

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
def normalize_status_kelancaran(
    status: Optional[str], 
    catatan: Optional[str], 
    jumlah_tersendat: int = 0, 
    jumlah_teguran: int = 0
) -> str:
    catatan_lower = (catatan or "").lower()
    current_status = (status or "").lower().strip()

    if jumlah_tersendat == 0 and jumlah_teguran == 0:
        if any(keyword in catatan_lower for keyword in ["ulang", "salah", "banyak kesalahan", "kesulitan"]):
            return "ulang" if ("ulang" in catatan_lower or "banyak" in catatan_lower) else "tersendat"
        if current_status in ["ulang", "gagal"]:
            return "ulang"
        if current_status in ["tersendat", "sedang"]:
            return "tersendat"
        return "lancar"

    if jumlah_tersendat >= 12 or jumlah_teguran >= 25 or current_status == "ulang":
        return "ulang"
    
    if 1 <= jumlah_tersendat < 12 or 1 <= jumlah_teguran < 25 or current_status == "tersendat":
        return "tersendat"
        
    return "lancar"

def get_session_username(request: Request, session_user: Optional[str] = None) -> Optional[str]:
    return session_user or request.headers.get("x-session-user") or request.cookies.get("x-session-user")


def get_quran_mapping_from_db(
    session: Session, 
    juz: Optional[int], 
    kaca: Optional[int], 
    bagian: Optional[str] = None,
    surah_id: Optional[int] = None
) -> dict:
    if not kaca and not juz and not surah_id:
        return {"surah_id": None, "surah_sahih": None, "ayat_standar": "-", "kaca": None}

    mapping = None

    if surah_id and kaca:
        pages_in_surah = session.exec(
            select(QuranPage)
            .where(QuranPage.surah_id == surah_id)
            .order_by(QuranPage.id.asc())
        ).all()
        
        if pages_in_surah and 1 <= kaca <= len(pages_in_surah):
            mapping = pages_in_surah[kaca - 1]

    if not mapping and kaca:
        query = select(QuranPage).where(QuranPage.kaca == kaca)
        if juz:
            query = query.where(QuranPage.juz == juz)
        if surah_id:
            query = query.where(QuranPage.surah_id == surah_id)
        mapping = session.exec(query).first()

    if not mapping and juz:
        mapping = session.exec(select(QuranPage).where(QuranPage.juz == juz)).first()

    if not mapping:
        return {"surah_id": None, "surah_sahih": None, "ayat_standar": "-", "kaca": None}

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
        "ayat_standar": a_range,
        "kaca": mapping.kaca
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


def clean_repeated_voice_text(text: str) -> str:
    if not text:
        return ""
    
    words = text.strip().split()
    dedup_words = []
    for w in words:
        if not dedup_words or dedup_words[-1].lower() != w.lower():
            dedup_words.append(w)
    text = " ".join(dedup_words)
    
    sentences = re.split(r'[,.\n]+', text)
    cleaned_sentences = []
    seen = set()
    for s in sentences:
        s_clean = s.strip()
        if not s_clean:
            continue
        s_lower = s_clean.lower()
        is_duplicate = False
        for seen_s in seen:
            if fuzz.ratio(s_lower, seen_s) > 80:
                is_duplicate = True
                break
        if not is_duplicate:
            seen.add(s_lower)
            cleaned_sentences.append(s_clean)
            
    text = ". ".join(cleaned_sentences)
    
    words = text.split()
    n = len(words)
    if n > 4:
        mid = n // 2
        first_half = " ".join(words[:mid]).lower()
        second_half = " ".join(words[mid:]).lower()
        if fuzz.ratio(first_half, second_half) > 75:
            text = " ".join(words[:mid])
            
    return text

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
# 2. VOICE PARSING COMMAND (ULTRA STRICT AI)
# ==========================================
@router.post("/parse-voice")
async def parse_voice_command(
    data: VoiceParseRequest, 
    session: Session = Depends(get_session)
):
    raw_command_text = data.get_text()
    command_text = clean_repeated_voice_text(raw_command_text)

    if not command_text.strip():
        raise HTTPException(status_code=422, detail="Teks transkrip suara kosong!")

    # SYSTEM PROMPT YANG DIPERKETAT SEMAKSIMAL MUNGKIN
    system_prompt = """
[SYSTEM PROTOCOL: ZERO-TOLERANCE STRICT JSON PARSER]
Anda adalah mesin ekstraksi data mutabaah Al-Quran otomatis yang sangat kaku, presisi, dan deterministik. 
Tugas mutlak Anda adalah mengekstrak transkrip suara musyrif ke dalam format JSON murni tanpa ada teks tambahan di luar JSON.

ATURAN KETAT:
1. JANGAN menambahkan markdown, catatan kaki, salam, atau penjelasan naratif di luar blok JSON. Output harus 100% parseable oleh `json.loads()`.
2. Ekstraksi field berikut dengan akurasi mutlak:
   - "juz": integer atau null jika tidak disebut.
   - "kaca": integer nomor halaman mushaf (jika disebut) atau null.
   - "bagian": string persis "a", "b", atau null.
   - "surah_langsung": string nama surah asli dari transkrip atau null.
   - "ayat_langsung": string rentang ayat (contoh: "1-10", "5") atau "-".
   - "status_kelancaran": WAJIB bernilai salah satu dari: "lancar", "tersendat", "gagal".
   - "jumlah_tersendat": integer (hitung akumulasi kata salah/ulang/tersendat).
   - "jumlah_teguran": integer (hitung akumulasi teguran tajwid/makhraj).
   - "catatan_musyrif": ringkasan profesional narasi musyrif dalam Bahasa Indonesia.
   - "tajwid_details": Array of object [{ "rule": "...", "error_description": "...", "count": int }]
   - "makhraj_details": Array of object [{ "letter": "...", "error_description": "...", "count": int }]

FORMAT JSON WAJIB:
{
    "juz": null,
    "kaca": null,
    "bagian": null,
    "surah_langsung": null,
    "ayat_langsung": "-",
    "status_kelancaran": "lancar",
    "jumlah_tersendat": 0,
    "jumlah_teguran": 0,
    "catatan_musyrif": "",
    "tajwid_details": [],
    "makhraj_details": []
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
            {"role": "user", "content": f"Transkrip Suara Musyrif: \"{command_text}\""}
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"}
    }

    try:
        if OPENROUTER_API_KEY:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
                
                if resp.status_code == 200:
                    res_json = resp.json()
                    content = res_json['choices'][0]['message']['content'] if 'choices' in res_json and res_json['choices'] else ""
                    
                    content_clean = content.strip()
                    if content_clean.startswith("```json"): content_clean = content_clean[7:]
                    if content_clean.startswith("```"): content_clean = content_clean[3:]
                    if content_clean.endswith("```"): content_clean = content_clean[:-3]
                    
                    raw_parsed = json.loads(content_clean.strip())

                    if isinstance(raw_parsed, list):
                        raw_parsed = raw_parsed[0] if len(raw_parsed) > 0 else {}
                    elif not isinstance(raw_parsed, dict):
                        raw_parsed = {}
                        
                    raw_surah = raw_parsed.get("surah_langsung")
                    raw_kaca = raw_parsed.get("kaca")
                    raw_juz = raw_parsed.get("juz")
                    raw_ayat = raw_parsed.get("ayat_langsung")
                        
                    ai_kelancaran = str(raw_parsed.get("status_kelancaran", "lancar")).lower()
                    
                    try:
                        jumlah_tersendat = int(raw_parsed.get("jumlah_tersendat", 0))
                    except (ValueError, TypeError):
                        jumlah_tersendat = 0

                    try:
                        jumlah_teguran = int(raw_parsed.get("jumlah_teguran", 0))
                    except (ValueError, TypeError):
                        jumlah_teguran = 0

                    catatan_eks = str(raw_parsed.get("catatan_musyrif", ""))

                    final_kelancaran = normalize_status_kelancaran(
                        status=ai_kelancaran, 
                        catatan=catatan_eks, 
                        jumlah_tersendat=jumlah_tersendat, 
                        jumlah_teguran=jumlah_teguran
                    )

                    bagian_val = str(raw_parsed.get("bagian")).lower() if raw_parsed.get("bagian") else None
                    
                    kaca_clean = None
                    if raw_kaca is not None:
                        match = re.search(r'(\d+)', str(raw_kaca))
                        if match:
                            kaca_clean = int(match.group(1))

                    juz_clean = None
                    if raw_juz is not None:
                        match = re.search(r'(\d+)', str(raw_juz))
                        if match:
                            juz_clean = int(match.group(1))

                    surah_info = None
                    surah_id_found = None

                    if raw_surah:
                        fuzzy_match = find_best_surah_match(raw_surah)
                        if fuzzy_match:
                            surah_id_found = fuzzy_match["surah_id"]
                            surah_info = {
                                "surah_id": surah_id_found,
                                "surah": fuzzy_match["surah_sahih"],
                                "ayat": raw_ayat or "-"
                            }

                    if kaca_clean or juz_clean:
                        db_mapping = get_quran_mapping_from_db(
                            session=session,
                            juz=juz_clean,
                            kaca=kaca_clean,
                            bagian=bagian_val,
                            surah_id=surah_id_found
                        )

                        if db_mapping.get("surah_id"):
                            surah_info = {
                                "surah_id": db_mapping["surah_id"],
                                "surah": db_mapping["surah_sahih"],
                                "ayat": db_mapping["ayat_standar"]
                            }
                    if not surah_info or not surah_info.get("surah_id"):
                        fallback_match = find_best_surah_match(command_text, cutoff_score=60.0)
                        if fallback_match:
                            surah_info = {
                                "surah_id": fallback_match["surah_id"],
                                "surah": fallback_match["surah_sahih"],
                                "ayat": raw_ayat or "-"
                            }
                        else:
                            surah_info = surah_info or {"surah_id": None, "surah": "-", "ayat": "-"}

                    final_draft = {
                        "surah": surah_info.get("surah"),
                        "ayat": surah_info.get("ayat"),
                        "status_kelancaran": final_kelancaran,
                        "jumlah_tersendat": jumlah_tersendat,
                        "jumlah_teguran": jumlah_teguran,
                        "catatan_musyrif": raw_parsed.get("catatan_musyrif") or "",
                        "kata_bermasalah": raw_parsed.get("kata_bermasalah", []),
                        "catatan_waqaf_ibtida": raw_parsed.get("catatan_waqaf_ibtida"),
                        "saran_perbaikan": raw_parsed.get("saran_perbaikan"),
                        "tajwid_details": raw_parsed.get("tajwid_details", []),
                        "makhraj_details": raw_parsed.get("makhraj_details", [])
                    }
                    
                    return {"status": "success", "mode": "combined", "draft": final_draft}
                
    except Exception as e:
        print(f"❌ VOICE PARSE ERROR: {e}")

    fallback_surah = find_best_surah_match(command_text, cutoff_score=60.0)
    return {
        "status": "error_fallback",
        "message": "AI Parser error/fallback",
        "draft": {
            "surah": fallback_surah["surah_sahih"] if fallback_surah else "-",
            "ayat": "-",
            "status_kelancaran": "tersendat",
            "jumlah_tersendat": 1,
            "jumlah_teguran": 0,
            "catatan_musyrif": command_text,
            "kata_bermasalah": [],
            "catatan_waqaf_ibtida": None,
            "saran_perbaikan": None,
            "tajwid_details": [],
            "makhraj_details": []
        }
    }

# ==========================================
# 3. INPUT SETORAN
# ==========================================
@router.post("/setoran")
async def input_setoran(
    request: Request,
    data: SetoranCreate,
    session: Session = Depends(get_session),
    session_user: Optional[str] = Cookie(None) 
):
    try:
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
            raise HTTPException(
                status_code=400, 
                detail=f"Tidak dapat menginput setoran! Santri berstatus '{santri.status_santri}'."
            )

        final_surah = data.surah_sahih or data.surah or "Surah Tidak Terdefinisi"
        final_ayat = data.ayat_standar or data.ayat or "-"
        now_wib = now_indonesia()

        detail_payload_dict = {
            "tajwid_details": [item.model_dump() for item in data.tajwid_details],
            "makhraj_details": [item.model_dump() for item in data.makhraj_details]
        }

        jumlah_tersendat = data.jumlah_tersendat
        jumlah_teguran = data.jumlah_teguran

        if jumlah_tersendat == 0 and data.tajwid_details:
            jumlah_tersendat = sum(item.count for item in data.tajwid_details)
        if jumlah_teguran == 0 and data.makhraj_details:
            jumlah_teguran = sum(item.count for item in data.makhraj_details)

        if (jumlah_tersendat == 0 or jumlah_teguran == 0) and data.catatan_musyrif:
            catatan_lower = data.catatan_musyrif.lower()
            
            if jumlah_tersendat == 0:
                if "banyak" in catatan_lower or "ulang" in catatan_lower:
                    jumlah_tersendat = 8
                elif "salah" in catatan_lower or "tersendat" in catatan_lower:
                    jumlah_tersendat = 3

            if jumlah_teguran == 0:
                if "makhraj" in catatan_lower or "huruf" in catatan_lower or "qalqalah" in catatan_lower:
                    jumlah_teguran = 2

        final_status_kelancaran = normalize_status_kelancaran(
            status=data.status_kelancaran,
            catatan=data.catatan_musyrif,
            jumlah_tersendat=jumlah_tersendat,
            jumlah_teguran=jumlah_teguran
        )

        new_setoran = SetoranTahfizh(
            santri_id=target_santri_id,
            surah=final_surah,
            ayat=final_ayat,
            status_kelancaran=final_status_kelancaran,
            catatan_musyrif=data.catatan_musyrif or "",
            jumlah_tersendat=jumlah_tersendat,
            jumlah_teguran=jumlah_teguran,
            ai_rekomendasi=f"Input terverifikasi. Tanggal: {now_wib.strftime('%d-%m-%Y %H:%M WIB')}"
        )
        session.add(new_setoran)
        session.commit()
        session.refresh(new_setoran)

        return {
            "status": "success", 
            "message": "Setoran berhasil dicatat!", 
            "data": new_setoran,
            "rincian_evaluasi": detail_payload_dict
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"❌ [ERROR /setoran]: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

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
                "status": normalize_status_kelancaran(
                    s.status_kelancaran, 
                    getattr(s, "catatan_musyrif", ""), 
                    getattr(s, "jumlah_tersendat", 0), 
                    getattr(s, "jumlah_teguran", 0)
                ),
                "jumlah_tersendat": getattr(s, "jumlah_tersendat", 0),
                "jumlah_teguran": getattr(s, "jumlah_teguran", 0),
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

    # Ambil seluruh setoran santri pada tanggal tersebut
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

    # Akumulasi metrik dari semua setoran hari ini
    total_setoran = len(setoran_today)
    total_tersendat = sum(getattr(s, 'jumlah_tersendat', 0) for s in setoran_today)
    total_teguran = sum(getattr(s, 'jumlah_teguran', 0) for s in setoran_today)

    catatan_setoran_teks = "\n".join([
        f"- Surah {s.surah}:{s.ayat} | Status: {s.status_kelancaran} | Tersendat: {getattr(s, 'jumlah_tersendat', 0)} | Teguran: {getattr(s, 'jumlah_teguran', 0)} | Catatan Musyrif: {s.catatan_musyrif or 'Tidak ada'}"
        for s in setoran_today
    ]) if setoran_today else "Belum/Tidak ada setoran hafalan hari ini."

    target_harian = getattr(santri, 'target_harian', 'Belum diset')

    # SYSTEM PROMPT YANG DIPERKETAT UNTUK AKUMULASI
    system_prompt = """
[SYSTEM PROTOCOL: ZERO-TOLERANCE STRICT JSON EVALUATOR]
Anda adalah AI Evaluator Tahfizh Harian yang kaku, profesional, dan taat aturan skema JSON.
Tugas Anda menganalisis akumulasi seluruh data setoran santri hari ini dan mengembalikan respons HANYA berupa objek JSON valid tanpa teks pembuka, penutup, atau markdown backticks.

ATURAN MUTLAK BAHASA & KONTEKS:
1. Gunakan HANYA istilah yang berkaitan dengan Al-Qur'an, Tajwid, dan Tahfizh (contoh: makhraj huruf, tajwid, mad, qalqalah, kelancaran).
2. DILARANG KERAS menggunakan kata-kata atau istilah yang melenceng jauh dari konteks pesantren/tahfizh (seperti kata "pernikahan" atau istilah ngaco lainnya). Jika catatan musyrif memiliki typo atau ambigu, fokuskan analisis pada metrik angka (jumlah tersendat dan teguran) serta hukum tajwid yang valid.
3. Gunakan bahasa Indonesia yang formal, objektif, dan konstruktif.

ATURAN KETAT STRUKTUR JSON:
{
  "kualitas_hafalan": "lancar" | "tersendat" | "ulang",
  "kualitas_tajwid": ["Nama Hukum Tajwid 1", "Nama Hukum Tajwid 2"],
  "absensi": "Hadir / Izin / Sakit / Alfa",
  "konsistensi_halaqoh": "Penilaian singkat kehadiran & keaktifan (maksimal 10 kata)",
  "konsistensi_setoran": "Penilaian singkat pencapaian target berdasarkan akumulasi setoran hari ini (maksimal 10 kata)",
  "rangkuman_teks": "Narasi ringkas objektif 2-3 kalimat mengenai akumulasi hafalan dan evaluasi hari ini."
}
"""

    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Nama: {santri.nama_santri}\nTanggal: {target_date}\nAbsensi: {status_absensi} ({keterangan_absensi})\nTarget Harian: {target_harian}\nTotal Sesi Setoran Hari Ini: {total_setoran}\nAkumulasi Total Tersendat: {total_tersendat}\nAkumulasi Total Teguran: {total_teguran}\nDetail Riwayat Setoran Hari Ini:\n{catatan_setoran_teks}"}
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"}
    }

    evaluasi_json = {
        "kualitas_hafalan": "lancar",
        "kualitas_tajwid": [],
        "absensi": status_absensi,
        "konsistensi_halaqoh": "Hadir",
        "konsistensy_setoran": "Belum ada setoran",
        "rangkuman_teks": "Hari ini tidak ada aktivitas setoran hafalan yang tercatat."
    }

    try:
        if OPENROUTER_API_KEY:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
                if resp.status_code == 200:
                    res = resp.json()
                    content = res['choices'][0]['message']['content'].strip()
                    if content.startswith("```json"): content = content[7:]
                    if content.startswith("```"): content = content[3:]
                    if content.endswith("```"): content = content[:-3]
                    evaluasi_json = json.loads(content.strip())
    except Exception as e:
        print(f"❌ Error AI Rangkuman: {e}")

    return {
        "status": "success",
        "tanggal": target_date.strftime("%Y-%m-%d"),
        "santri_id": santri_id,
        "total_setoran_hari_ini": total_setoran,
        "akumulasi_tersendat": total_tersendat,
        "akumulasi_teguran": total_teguran,
        "rangkuman_ai": evaluasi_json.get("rangkuman_teks", ""),
        "penilaian_harian_ai": evaluasi_json 
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

    prompt_copilot = "Anda adalah AI Evaluator Raport Tahfizh yang profesional, solutif, berbasis data objektif, dan langsung pada poin esensial tanpa basa-basi."

    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": prompt_copilot},
            {"role": "user", "content": f"Buatkan rangkuman evaluasi raport untuk santri ID {data.santri_id}"}
        ],
        "temperature": 0.1
    }

    rekomendasi_generated = "Gagal memproses rekomendasi AI."
    try:
        if OPENROUTER_API_KEY:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
                if resp.status_code == 200:
                    rekomendasi_generated = resp.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
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

    final_rekomendasi_ai = data.rekomendasi_ai or "Analisa AI tersimpan."

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
        return {"status": "success", "message": "Nilai Raport berhasil diperbarui!", "data": existing_raport}

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
# 8. PROFILE DETAIL SANTRI & UPLOAD FOTO
# ==========================================
@router.get("/santri/{santri_id}/profile")
def get_profile_santri(santri_id: int, session: Session = Depends(get_session)):
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
                "status_kelancaran": normalize_status_kelancaran(
                    s.status_kelancaran, 
                    getattr(s, 'catatan_musyrif', ''), 
                    getattr(s, 'jumlah_tersendat', 0), 
                    getattr(s, 'jumlah_teguran', 0)
                ),
                "jumlah_tersendat": getattr(s, 'jumlah_tersendat', 0),
                "jumlah_teguran": getattr(s, 'jumlah_teguran', 0),
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

    santri = session.get(Santri, santri_id)
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan")

    target_file = file or foto
    if not target_file:
        raise HTTPException(status_code=400, detail="File gambar wajib diunggah!")

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
# 9. PROFILE MUSYRIF & CHANGE PASSWORD
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
        raise HTTPException(status_code=400, detail="File gambar wajib diunggah!")

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

# Append APIRouter ke App Utama
app.include_router(router)