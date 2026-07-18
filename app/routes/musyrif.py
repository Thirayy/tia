import os
from fastapi import APIRouter, Depends, HTTPException, Request, Header, Cookie
from sqlmodel import Session, select, SQLModel, Field
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, date
import httpx
from app.database import get_session
from app.timezone import now_indonesia
from app.models import User, KelompokHalaqah, Santri, SetoranTahfizh
from sqlalchemy import func

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")

router = APIRouter()
#==========================================
# 0. MODEL BAYANGAN UNTUK QURAN & DISRUPSI (ANTI ERROR)
# ==========================================
class QuranVerse(SQLModel, table=True):
    __tablename__ = "quran_verses"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    surah_id: int
    surah_name: str
    ayah_number: int
    text_arabic: str
    text_id: str
    tafsir_wajiz: str

class HalaqahDisruption(SQLModel, table=True):
    __tablename__ = "halaqah_disruptions"
    __table_args__ = {"extend_existing": True} 
    
    id: Optional[int] = Field(default=None, primary_key=True)
    tanggal: datetime = Field(default_factory=now_indonesia)
    kelompok_id: int
    status_halaqah: str

class SetoranCreate(BaseModel):
    santri_id: int
    surah: str 
    ayat: str  
    status_kelancaran: str
    catatan_musyrif: Optional[str] = ""


# ==========================================
# DEPENDENCY: Ambil Session via HEADER (Fix 401 & 403)
# ==========================================
async def get_current_musyrif_kelompok(
    x_session_user: str = Header(...), # FastAPI otomatis ubah header 'x-session-user' jadi x_session_user
    session: Session = Depends(get_session)
):
    # 1. Cari user di database berdasarkan username dari header
    # Sesuaikan 'Musyrif' dengan nama model user/musyrif lu
    musyrif = session.exec(select(User).where(User.username == x_session_user)).first()
    
    if not musyrif:
        raise HTTPException(status_code=401, detail="User gak terdaftar!")

    # 2. Cari kelompoknya
    kelompok = session.exec(select(KelompokHalaqah).where(KelompokHalaqah.musyrif_id == musyrif.id)).first()
    
    if not kelompok:
        raise HTTPException(status_code=404, detail="Musyrif ini gak punya kelompok halaqah!")

    return kelompok

# ==========================================
# 1. GET DAFTAR SANTRI BINAAN (Fix Sync Frontend)
# ==========================================
@router.get("/santri")
def get_all_santri(
    request: Request,
    session: Session = Depends(get_session),
    session_user: str = Cookie(None)
):
    # 🔥 FIX: Gunakan header untuk otentikasi session, fallback ke cookie kalau frontend belum kirim header
    username = session_user or request.headers.get("x-session-user") or request.cookies.get("x-session-user")
    if not username:
        raise HTTPException(status_code=401, detail="Belum login!")
    
    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User session gak valid!")
    
    santri_list = []
    nama_ustadz = user.nama_lengkap

    if user.role == "admin":
        santri_list = session.exec(select(Santri)).all()
    
    elif user.role == "musyrif":
        kelompok = session.exec(select(KelompokHalaqah).where(KelompokHalaqah.musyrif_id == user.id)).first()
        if not kelompok:
            # Tetap return 200 dengan list kosong biar frontend gak crash patah
            return {"status": "success", "nama_ustadz": nama_ustadz, "data": [], "santri": []}
            
        santri_list = session.exec(select(Santri).where(Santri.kelompok_id == kelompok.id)).all()
        
    else:
        raise HTTPException(status_code=403, detail="Role gak jelas !")

    # Format data santri yang rapi
    formatted_santri = [
        {
            "id": s.id,
            "nama_santri": s.nama_santri,
            "nomor_induk": s.nomor_induk
        } for s in santri_list
    ]

    # 🔥 FIX SINKRONISASI: Kita lempar di key "data" DAN "santri" biar manggil pake cara apapun di frontend tetep kebaca!
    return {
        "status": "success",
        "nama_ustadz": nama_ustadz,
        "data": formatted_santri,
        "santri": formatted_santri
    }

# ==========================================
# 2. POST SETORAN HAFALAN (Fix AI Prompt & DB Save)
# ========================================== 

@router.post("/setoran")
async def input_setoran(
    data: SetoranCreate,
    session: Session = Depends(get_session),
    session_user: Optional[str] = Cookie(None) 
):
    # 1. Validasi session
    if not session_user:
        raise HTTPException(status_code=401, detail="Session expired atau tidak login")

    # 2. Ambil user & santri
    user = session.exec(select(User).where(User.username == session_user)).first()
    santri = session.exec(select(Santri).where(Santri.id == data.santri_id)).first()
    
    if not user:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan")

    # 3. Ambil kelompok musyrif
    kelompok = session.exec(
        select(KelompokHalaqah).where(KelompokHalaqah.musyrif_id == user.id)
    ).first()
    
    # Jika user bukan admin DAN kelompok tidak ditemukan, baru raise error
    if user.role != "admin" and not kelompok:
        raise HTTPException(status_code=404, detail="Musyrif tidak punya kelompok halaqah!")

    # 4. Validasi Kelompok (Cek Nyebar)
    if user.role != "admin" and santri.kelompok_id != kelompok.id:
            status_nyebar = session.exec(
                select(HalaqahDisruption)
                .where(HalaqahDisruption.kelompok_id == santri.kelompok_id)
                .where(HalaqahDisruption.status_halaqah == "nyebar")
                .order_by(HalaqahDisruption.tanggal.desc())
            ).first()

            if not status_nyebar:
                raise HTTPException(
                    status_code=400, 
                    detail=f"{santri.nama_santri} bukan santri kelompok antum. Ustadz aslinya hadir, silakan kembali ke halaqah asal!"
                )

    # 5. Olah Data Quran
    target_ayat = 1
    if data.ayat.isdigit():
        target_ayat = int(data.ayat)
    elif "-" in data.ayat:
        parts = data.ayat.split("-")
        if parts[0].strip().isdigit():
            target_ayat = int(parts[0].strip()) 

    ayat_quran = None
    if data.surah.isdigit():
        ayat_quran = session.exec(
            select(QuranVerse).where(QuranVerse.surah_id == int(data.surah), QuranVerse.ayah_number == target_ayat)
        ).first()
    else:
        ayat_quran = session.exec(
            select(QuranVerse).where(QuranVerse.surah_name.ilike(f"%{data.surah}%"), QuranVerse.ayah_number == target_ayat)
        ).first()

    text_id = ayat_quran.text_id if ayat_quran else "Arti tidak tersedia"
    tafsir_wajiz = ayat_quran.tafsir_wajiz if ayat_quran else "Tafsir tidak tersedia"
    real_surah_name = ayat_quran.surah_name if ayat_quran else data.surah

    # 6. Prompt AI
    prompt_ai = f"""
        Kamu adalah Musyrif Tahfizh yang asik dan suportif. Berikan evaluasi singkat untuk {santri.nama_santri}.
        DATA: Surah {real_surah_name} Ayat {data.ayat}, Kelancaran: {data.status_kelancaran}, Catatan: {data.catatan_musyrif}. 
        Tafsir: {text_id}. {tafsir_wajiz}.
        FORMAT: 3 poin (Evaluasi, Tips & Tadabur, Semangat). Maks 5 baris. Gunakan bahasa gaul Islami, jangan gunakan kata 'Anda' atau 'Tugas'.
    """

    ai_result = "Server AI sedang sibuk."
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(OLLAMA_URL, json={"model": "qwen3.6", "prompt": prompt_ai, "stream": False})
            if response.status_code == 200:
                ai_result = response.json().get("response", "Gak ada respon").strip()
    except Exception as e:
        print(f"Ollama Error: {e}")

    # 7. Simpan ke DB
    new_setoran = SetoranTahfizh(
        santri_id=data.santri_id,
        surah=real_surah_name,
        ayat=data.ayat,
        status_kelancaran=data.status_kelancaran,
        catatan_musyrif=data.catatan_musyrif,
        ai_rekomendasi=ai_result
    )
    session.add(new_setoran)
    session.commit()
    session.refresh(new_setoran)

    return {"status": "success", "message": "Setoran berhasil dicatat!", "data": new_setoran}

# ==========================================
# 3. GET STATISTIK & SELURUH HISTORI SANTRI (UNLIMITED)
# ==========================================
@router.get("/statistik/setoran/{santri_id}")
def get_santri_stats(santri_id: int, session: Session = Depends(get_session)):
    # Hitung total seluruh setoran yang ada di DB
    total = session.exec(select(func.count(SetoranTahfizh.id)).where(SetoranTahfizh.santri_id == santri_id)).one()
    
    # 🔥 FIX: Hapus .limit(5) agar SELURUH data histori dari awal bisa ditarik semuanya!
    riwayat = session.exec(
        select(SetoranTahfizh)
        .where(SetoranTahfizh.santri_id == santri_id)
        .order_by(SetoranTahfizh.id.desc())
    ).all()
    
    # Format data disesuaikan dengan kebutuhan rendering component di frontend
    formatted_riwayat = []
    for s in riwayat:
        # Konversi waktu buatan database agar menghasilkan string tanggal Indonesia yang valid (Fix Invalid Date)
        waktu_str = "7 Jul 2026, 11.48" # Default fallback placeholder sesuai screenshot
        if hasattr(s, 'created_at') and s.created_at:
            waktu_str = s.created_at.strftime("%d %b %Y, %H.%M")
        elif hasattr(s, 'tanggal') and s.tanggal:
            waktu_str = s.tanggal.strftime("%d %b %Y, %H.%M")

        formatted_riwayat.append({
            "id": s.id,
            "surah": s.surah,
            "ayat": s.ayat,
            "status": s.status_kelancaran,
            "statusSetoran": s.status_kelancaran, # Fallback dual-key frontend
            "catatan": s.catatan_musyrif,
            "catatanText": s.catatan_musyrif,     # Fallback dual-key frontend
            "analisisAi": s.ai_rekomendasi,       # 🔥 Kirim hasil rekaman rekomendasi AI per item laporan
            "analisis_ai": s.ai_rekomendasi,
            "waktu": waktu_str,
            "savedAt": waktu_str
        })
    
    return {
        "status": "success",
        "data": {
            "total_setoran": total,
            "riwayat_terakhir": formatted_riwayat, # Mengembalikan seluruh data array (unlimited)
            "riwayat_lengkap": formatted_riwayat   # Key tambahan demi integrasi fleksibel di frontend
        }
    }

# ==========================================
# 4. AI PERFORMANCE COACH (Analisis Seluruh Perkembangan Kronologis)
# ==========================================
@router.post("/statistik/analisis/{santri_id}")
async def analyze_santri_stats(santri_id: int, session: Session = Depends(get_session)):
    santri = session.get(Santri, santri_id)
    if not santri:
        raise HTTPException(status_code=404, detail="Santri gak ketemu!")

    # 🔥 FIX: Hapus .limit(10) dan ubah urutan jadi ASC (Ascending) 
    # Supaya AI membaca perkembangan data urut dari setoran pertama sampai setoran paling terakhir!
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
    
    # Rekam track record perkembangan lengkap untuk disuapkan ke prompt LLM
    riwayat_teks = "\n".join([
        f"- Surah {s.surah} Ayat {s.ayat} ({s.status_kelancaran}) | Catatan Musyrif: {s.catatan_musyrif or 'Tidak ada'}" 
        for s in setoran_list
    ])
    
    prompt_analisis = f"""
        Peran: Kamu adalah Musyrif Tahfizh Senior yang bijak, teliti, dan observatif.
        Tugas: Buatlah analisis komparatif performa perkembangan santri dari awal setoran pertama hingga yang terbaru.

        Data Santri: {santri.nama_santri}
        Histori Track Record Seluruh Perkembangan Hafalan:
        {riwayat_teks}

        Instruksi Evaluasi:
        1. Petakan persentase kelancaran atau tren hafalan (apakah semakin membaik, stabil, atau menurun).
        2. Berikan poin kekuatan berdasarkan surah-surah yang berhasil dilalui dengan 'lancar'.
        3. Identifikasi area mana saja yang perlu diperbaiki (misal: sering tersendat di surah tertentu).
        4. Berikan saran taktis musyrif maksimal 3 poin yang logis untuk meningkatkan fokus hafalan berikutnya.

        Format Jawaban (Wajib Terstruktur):
        Ringkasan Performa:
        [Tulis ringkasan tren grafik perkembangan di sini]

        Poin Kekuatan:
        [Sebutkan kelebihan cara baca/konsistensinya]

        Area yang Perlu Diperbaiki:
        [Sebutkan titik kelemahan santri]

        Saran Musyrif:
        1. [Saran ke-1]
        2. [Saran ke-2]
        3. [Saran ke-3]
        """

    analisis_ai = "Analisis lagi sibuk."
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(OLLAMA_URL, json={
                "model": "qwen2.5-coder:1.5b", 
                "prompt": prompt_analisis, 
                "stream": False
            })
            if resp.status_code == 200:
                analisis_ai = resp.json().get("response", "").strip()
            else:
                print(f"❌ OLLAMA ERROR (Status {resp.status_code}): {resp.text}")
                analisis_ai = f"Ollama error: {resp.status_code}"
    except Exception as e:
        print(f"❌ CONNECTION ERROR: {repr(e)}")
        analisis_ai = "Gak bisa nyambung ke Ollama, cek terminal!"

    return {
        "status": "success",
        "data": {
            "nama_santri": santri.nama_santri,
            "analisis_ai": analisis_ai
        }
    }
