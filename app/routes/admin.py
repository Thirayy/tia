from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select, SQLModel, Field, text, delete
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy import func, text
from app.models import Santri, KelompokHalaqah, User, SetoranTahfizh
from app.timezone import now_indonesia, format_indonesia
from app.security import hash_password

# Pastikan import engine untuk auto-create tabel baru
from app.database import get_session, engine 
from app.models import User, KelompokHalaqah, Santri

router = APIRouter()

# ==========================================
# 0. MODEL BARU: LOG DISRUPSI (AUTO CREATE)
# ==========================================
class HalaqahDisruption(SQLModel, table=True):
    __tablename__ = "halaqah_disruptions"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    tanggal: datetime = Field(default_factory=now_indonesia)
    kelompok_id: int
    musyrif_id: int          # Musyrif asli yang berhalangan
    badal_musyrif_id: Optional[int] = None # Musyrif pengganti (kalau ada)
    alasan: str              # Contoh: "Rapat Guru Bahasa Urgent"
    status_halaqah: str      # "diganti_badal" atau "diliburkan_total"

# BIAR OP & ANTI-ERROR: Langsung ciptakan tabel di Postgres kalau belum ada!
SQLModel.metadata.create_all(engine)


# ==========================================
# DEPENDENCY SAKTI: ANTI-401 & ANTI-403 BYPASS AUTO-CREATE ADMIN
# ==========================================
def get_current_admin(request: Request, session: Session = Depends(get_session)):
    username = request.cookies.get("session_user") or request.headers.get("x-session-user")
    nama_lengkap = request.headers.get("x-session-nama")
    
    if username in ["undefined", "", None]: username = None
    if nama_lengkap in ["undefined", "", None]: nama_lengkap = None
    
    user = None
    if username:
        user = session.exec(select(User).where(User.username == username)).first()
    if not user and nama_lengkap:
        user = session.exec(select(User).where(User.nama_lengkap == nama_lengkap)).first()
    
    if user and user.role == "admin":
        return user

    if user:
        raise HTTPException(status_code=403, detail="Akses admin diperlukan.")
    raise HTTPException(status_code=401, detail="Belum login sebagai admin.")


@router.get("/overview")
async def get_dashboard_overview(session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    try:
        # 1. Total Ustadz & Santri
        total_ustadz = len(session.exec(select(User).where(User.role == "musyrif")).all())
        semua_santri = session.exec(select(Santri)).all()
        total_santri = len(semua_santri)
        santri_aktif = len([s for s in semua_santri if getattr(s, 'status_santri', 'aktif') == 'aktif'])
        
        # 2. NGAMBIL DATA ASLI (Ganti hardcode 50 jadi hitungan real)
        total_laporan = session.exec(select(func.count(SetoranTahfizh.id))).one()
        
        # Untuk grafik, sementara kita hitung total saja, 
        # nanti bisa di-group by tanggal kalau mau lebih advance
        return {
            "status": "success",
            "counts": {
                "ustadz": total_ustadz,
                "santri": total_santri,
                "total_laporan": total_laporan, 
                "santri_aktif": santri_aktif
            },
            # Grafik ini nanti bisa dibuat query sendiri per hari
            "grafik_mingguan": [
                {"hari": "Total", "laporan": total_laporan} 
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal generate data: {str(e)}")
    
# ==========================================
# 2. CREATE MUSYRIF (Hanya 1 Jalur /musyrif)
# ==========================================
class MusyrifCreate(BaseModel):
    username: str
    password: str
    nama_lengkap: str

@router.post("/musyrif")
async def create_musyrif(data: MusyrifCreate, session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    cek_user = session.exec(select(User).where(User.username == data.username)).first()
    if cek_user:
        raise HTTPException(status_code=400, detail="Username musyrif udah ada yang pake!")
    
    new_musyrif = User(
        username=data.username,
        password_hash=hash_password(data.password),
        nama_lengkap=data.nama_lengkap,
        role="musyrif"
    )
    session.add(new_musyrif)
    session.commit()
    session.refresh(new_musyrif)
    
    return {"status": "success", "message": f"Musyrif {new_musyrif.nama_lengkap} berhasil didaftarkan!"}


# ==========================================
# 3. GET ALL MUSYRIF (Hanya 1 Jalur /musyrif)
# ==========================================
@router.get("/musyrif")
async def get_semua_musyrif(session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    try:
        statement = select(User).where((User.role == "musyrif") | (User.role == "admin"))
        list_pengguna = session.exec(statement).all()
        
        return {
            "status": "success",
            "data": [
                {
                    "id": u.id,
                    "username": u.username,
                    "nama_lengkap": u.nama_lengkap,
                    "role": u.role,
                    "tanggal_dibuat": format_indonesia(getattr(u, "created_at", None), "%d/%m/%Y")
                } for u in list_pengguna
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal narik data musyrif cok: {str(e)}")


# ==========================================
# 4. CREATE KELOMPOK (Bikin Halaqah & Map ke Musyrif)
# ==========================================
class KelompokCreate(BaseModel):
    nama_kelompok: str
    musyrif_id: int

@router.post("/kelompok")
async def create_kelompok(data: KelompokCreate, session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    musyrif = session.get(User, data.musyrif_id)
    if not musyrif or musyrif.role != "musyrif":
        raise HTTPException(status_code=404, detail="Musyrif tidak ditemukan!")
    
    cek_kelompok = session.exec(select(KelompokHalaqah).where(KelompokHalaqah.musyrif_id == data.musyrif_id)).first()
    if cek_kelompok:
        raise HTTPException(status_code=400, detail=f"Ustadz {musyrif.nama_lengkap} sudah memegang {cek_kelompok.nama_kelompok}!")
    new_kelompok = KelompokHalaqah(
        nama_kelompok=data.nama_kelompok,
        musyrif_id=data.musyrif_id
    )
    session.add(new_kelompok)
    session.commit()
    session.refresh(new_kelompok)
    
    return {"status": "success", "message": f"Kelompok {new_kelompok.nama_kelompok} berhasil dibuat!"}


# ==========================================
# GET KELOMPOK (Untuk Dropdown Selection di Frontend)
# ==========================================
@router.get("/kelompok")
async def get_semua_kelompok(session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    try:
        statement = select(KelompokHalaqah, User).join(User, KelompokHalaqah.musyrif_id == User.id, isouter=True)
        results = session.exec(statement).all()
        return {
            "status": "success",
            "data": [
                {
                    "id": k.id,
                    "nama_kelompok": k.nama_kelompok,
                    "nama_ustadz": u.nama_lengkap if u else "Tanpa Musyrif"
                } for k, u in results
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal memuat opsi kelompok halaqah: {str(e)}")


# ==========================================
# 5. CREATE SANTRI (Tambah Santri & Map ke Kelompok)
# ==========================================
class SantriCreate(BaseModel):
    nama_santri: str
    nomor_induk: str
    kelompok_id: int
    kelompok_id: Optional[int] = None

@router.post("/santri")
def create_santri(data: SantriCreate, session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    # 1. Cuma ngecek kalau kelompok_id ada isinya (bukan None/kosong)
    if data.kelompok_id: 
        kelompok = session.get(KelompokHalaqah, data.kelompok_id)
        if not kelompok:
            raise HTTPException(status_code=404, detail="Kelompok halaqah tidak ditemukan!")
    
    # 2. Kalau kelompok_id None, ya lanjut aja simpan santrinya!
    santri = Santri(
        nama_santri=data.nama_santri,
        nomor_induk=data.nomor_induk,
        kelompok_id=data.kelompok_id # Ini bakal jadi None kalau kosong
    )
    session.add(santri)
    session.commit()
    session.refresh(santri)
    return santri


# ==========================================
# 6. GET ALL SANTRI + RELASI JOIN 
# ==========================================
@router.get("/santri")
async def get_semua_santri(session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    try:
        # Kita tambahin count(SetoranTahfizh.id) dan join ke tabel setoran
        statement = (
            select(
                Santri, 
                KelompokHalaqah, 
                User, 
                func.count(SetoranTahfizh.id).label("total_setoran")
            )
            .join(KelompokHalaqah, Santri.kelompok_id == KelompokHalaqah.id, isouter=True)
            .join(User, KelompokHalaqah.musyrif_id == User.id, isouter=True)
            .join(SetoranTahfizh, Santri.id == SetoranTahfizh.santri_id, isouter=True) # <--- JOIN SETORAN
            .group_by(Santri.id, KelompokHalaqah.id, User.id) # <--- WAJIB GROUP BY
        )
        results = session.exec(statement).all()
        
        output = []
        for santri, kelompok, musyrif, total_setoran in results: # Tambahin variabel total_setoran
            output.append({
                "id": santri.id,
                "nama_santri": santri.nama_santri,
                "nomor_induk": santri.nomor_induk,
                "status_santri": getattr(santri, 'status_santri', 'aktif') or 'aktif',
                "kelompok_id": santri.kelompok_id,
                "nama_kelompok": kelompok.nama_kelompok if kelompok else "Belum Masuk Kelompok",
                "nama_ustadz": musyrif.nama_lengkap if musyrif else "Ustadz Belum Diplotting",
                "total_setoran": total_setoran # <--- Kirim ini ke frontend!
            })
            
        return {"status": "success", "data": output}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal memuat list data santri: {str(e)}")


# ==========================================
# 7. UPDATE PLOTTING / KELOMPOK SANTRI (Fix 404)    
# ==========================================
class SantriUpdate(BaseModel):
    student_id: Optional[int] = None
    nama_santri: Optional[str] = None
    nomor_induk: Optional[str] = None
    kelompok_id: Optional[int] = None  # Menampung ID Musyrif dari select frontend

@router.put("/santri/{santri_id}") # Hapus /api/admin dari sini!
def update_santri_plotting(
    santri_id: int,
    data: SantriUpdate,
    session: Session = Depends(get_session),
    admin: User = Depends(get_current_admin)
):
    santri = session.exec(select(Santri).where(Santri.id == santri_id)).first()

    if not santri:
        raise HTTPException(status_code=404, detail=f"Santri dengan ID {santri_id} gak ketemu!")
    
    # Update data
    if data.nama_santri: santri.nama_santri = data.nama_santri
    if data.nomor_induk: santri.nomor_induk = data.nomor_induk
    if data.kelompok_id is not None: santri.kelompok_id = data.kelompok_id
    
    session.commit()
    session.refresh(santri)
    return {"status": "success", "data": santri}

# ==========================================
# 8. POST DISRUPSI JADWAL
# ==========================================
class CatatDisrupsiRequest(BaseModel):
    kelompok_id: int
    badal_musyrif_id: Optional[int] = None
    alasan: str
    status_halaqah: str 

@router.post("/halaqah/disrupsi")
def catat_disrupsi_halaqah(data: CatatDisrupsiRequest, session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    kelompok = session.get(KelompokHalaqah, data.kelompok_id)
    if not kelompok:
        raise HTTPException(status_code=404, detail="Kelompok halaqah tidak ditemukan!")
        
    if data.badal_musyrif_id:
        badal = session.get(User, data.badal_musyrif_id)
        if not badal or badal.role != "musyrif":
            raise HTTPException(status_code=400, detail="User pengganti tidak valid atau bukan musyrif!")
        if badal.id == kelompok.musyrif_id:
            raise HTTPException(status_code=400, detail="Masa ustadz aslinya jadi badal buat dirinya sendiri cok? Pilih ustadz lain!")

    log_gangguan = HalaqahDisruption(
        kelompok_id=data.kelompok_id,
        musyrif_id=kelompok.musyrif_id,
        badal_musyrif_id=data.badal_musyrif_id,
        alasan=data.alasan,
        status_halaqah=data.status_halaqah
    )
    session.add(log_gangguan)
    session.commit()
    session.refresh(log_gangguan)
    return {"status": "success", "message": f"Disrupsi tercatat!", "data": log_gangguan}


# ==========================================
# 9. DELETE SANTRI (Hapus Data Santri)
# ==========================================
@router.delete("/santri/{santri_id}") 
def delete_santri(santri_id: int, session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    # 1. Cari dulu apakah santri ada
    santri = session.exec(select(Santri).where(Santri.id == santri_id)).first()
    if not santri:
        raise HTTPException(status_code=404, detail="Data santri tidak ditemukan")
    
    # 2. Hapus relasi (Setoran) terlebih dahulu untuk menghindari constraint error
    # Kita gunakan santri_id agar konsisten dengan model SetoranTahfizh
    session.exec(
        delete(SetoranTahfizh).where(SetoranTahfizh.santri_id == santri_id)
    )
    
    # 3. Hapus santri
    session.delete(santri)
    session.commit()
    
    return {"status": "success", "message": "Data berhasil dihapus!"}


@router.delete("/musyrif/{user_id}")
def delete_musyrif(user_id: int, session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Musyrif/User tidak ditemukan!")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Admin yang sedang login tidak bisa menghapus dirinya sendiri.")
    if user.role == "admin":
        total_admin = session.exec(select(func.count(User.id)).where(User.role == "admin")).one()
        if total_admin <= 1:
            raise HTTPException(status_code=400, detail="Minimal harus ada satu admin aktif.")

    kelompok_list = session.exec(select(KelompokHalaqah).where(KelompokHalaqah.musyrif_id == user.id)).all()
    for kelompok in kelompok_list:
        kelompok.musyrif_id = None
        session.add(kelompok)

    session.delete(user)
    session.commit()
    return {"status": "success", "message": "Data musyrif berhasil dihapus."}

    # ==========================================
# 10. GET MONITOR KELOLA HALAQAH (SUPER OVERVIEW)
# ==========================================
@router.get("/halaqah/monitor")
def get_monitor_halaqah(session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    try:
        kelompok_list = session.exec(select(KelompokHalaqah)).all()
        hasil = []
        
        for k in kelompok_list:
            musyrif_asli = session.get(User, k.musyrif_id)
            
            # Cari disrupsi / badal yang masih aktif
            badal_aktif = session.exec(
                select(HalaqahDisruption)
                .where(
                    HalaqahDisruption.kelompok_id == k.id, 
                    HalaqahDisruption.status_halaqah == "diganti_badal"
                )
                .order_by(HalaqahDisruption.id.desc())
            ).first()
            
            # Hitung total santri di halaqah ini
            total_santri = session.exec(select(func.count(Santri.id)).where(Santri.kelompok_id == k.id)).one()
            
            nama_badal = None
            if badal_aktif and badal_aktif.badal_musyrif_id:
                user_badal = session.get(User, badal_aktif.badal_musyrif_id)
                nama_badal = user_badal.nama_lengkap if user_badal else "Tidak Diketahui"
                
            hasil.append({
                "kelompok_id": k.id,
                "nama_kelompok": k.nama_kelompok,
                "musyrif_asli": musyrif_asli.nama_lengkap if musyrif_asli else "Belum Ada Ustadz",
                "total_santri": total_santri,
                "status_halaqah": "diganti_badal" if badal_aktif else "normal",
                "info_badal": {
                    "nama_badal": nama_badal,
                    "alasan": badal_aktif.alasan if badal_aktif else ""
                } if badal_aktif else None
            })
            
        return {"status": "success", "data": hasil}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal memuat data monitor halaqah: {str(e)}")
    
# ==========================================
# 11. PUT CANCEL BADAL (KEMBALI KE MUSYRIF ASLI)
# ==========================================
@router.put("/halaqah/{kelompok_id}/cancel-badal")
def cancel_badal_halaqah(kelompok_id: int, session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    # Cari log badal yang statusnya masih aktif
    statement = select(HalaqahDisruption).where(
        HalaqahDisruption.kelompok_id == kelompok_id,
        HalaqahDisruption.status_halaqah == "diganti_badal"
    ).order_by(HalaqahDisruption.id.desc())
    
    disrupsi = session.exec(statement).first()
    
    if not disrupsi:
        raise HTTPException(status_code=400, detail="Halaqah ini lagi gak di-badal, statusnya udah normal!")
        
    # Ubah statusnya jadi selesai
    disrupsi.status_halaqah = "selesai"
    
    session.add(disrupsi)
    session.commit()
    
    return {
        "status": "success", 
        "message": "Status badal berhasil dicancel! Halaqah kembali dipegang musyrif asli."
    }

# ==========================================
# 12. GET HISTORI LAPORAN PER HALAQAH
# ==========================================
@router.get("/halaqah/{kelompok_id}/laporan")
def get_laporan_per_halaqah(kelompok_id: int, limit: int = 50, session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    try:
        # Join tabel SetoranTahfizh dengan Santri untuk ambil laporan spesifik kelompok ini
        statement = (
            select(SetoranTahfizh, Santri)
            .join(Santri, SetoranTahfizh.santri_id == Santri.id)
            .where(Santri.kelompok_id == kelompok_id)
            .order_by(SetoranTahfizh.id.desc())
            .limit(limit)
        )
        
        results = session.exec(statement).all()
        
        laporan_list = []
        for setoran, santri in results:
            laporan_list.append({
                "id_setoran": setoran.id,
                "nama_santri": santri.nama_santri,
                "surah": setoran.surah,
                "ayat": setoran.ayat,
                "status_kelancaran": setoran.status_kelancaran,
                "catatan_musyrif": getattr(setoran, 'catatan_musyrif', None),
                "ai_rekomendasi": getattr(setoran, 'ai_rekomendasi', None),
                "waktu_setoran": format_indonesia(getattr(setoran, 'created_at', None))
            })
            
        return {"status": "success", "total_data": len(laporan_list), "data": laporan_list}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal narik histori laporan: {str(e)}")
# ==========================================
# 13. UPDATE ROLE MUSYRIF (Admin Only)
# ==========================================
class UpdateRolePayload(BaseModel):
    role: str

@router.put("/musyrif/{user_id}")
def change_musyrif_role(user_id: int, payload: UpdateRolePayload, session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    # 1. Cari user berdasarkan ID
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Musyrif/User tidak ditemukan cok!")
    
    # 2. Validasi input role biar ga aneh-aneh
    if payload.role not in ["admin", "musyrif"]:
        raise HTTPException(status_code=400, detail="Role harus 'admin' atau 'musyrif'!")

    # 3. Eksekusi ganti role
    user.role = payload.role
    session.add(user)
    session.commit()
    session.refresh(user)
    
    return {
        "status": "success", 
        "message": f"⚡ Berhasil ganti role {user.nama_lengkap} jadi {payload.role.upper()}!",
        "data": {"id": user.id, "username": user.username, "role": user.role}
    }
