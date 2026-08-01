from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select, SQLModel, Field, delete
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy import func

from app.models import Santri, KelompokHalaqah, User, SetoranTahfizh, HalaqahDisruption
from app.timezone import now_indonesia, format_indonesia
from app.security import hash_password
from app.database import get_session, engine 

router = APIRouter()

# Auto create tabel baru jika belum ada
SQLModel.metadata.create_all(engine)


# ==========================================
# DEPENDENCY: AUTH ADMIN
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


# ==========================================
# 1. OVERVIEW DASHBOARD
# ==========================================
@router.get("/overview")
async def get_dashboard_overview(session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    try:
        total_ustadz = len(session.exec(select(User).where(User.role == "musyrif")).all())
        semua_santri = session.exec(select(Santri)).all()
        total_santri = len(semua_santri)
        santri_aktif = len([s for s in semua_santri if getattr(s, 'status_santri', 'hadir') == 'hadir'])
        
        total_laporan = session.exec(select(func.count(SetoranTahfizh.id))).one()
        
        return {
            "status": "success",
            "counts": {
                "ustadz": total_ustadz,
                "santri": total_santri,
                "total_laporan": total_laporan, 
                "santri_aktif": santri_aktif
            },
            "grafik_mingguan": [
                {"hari": "Total", "laporan": total_laporan} 
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal generate data: {str(e)}")


# ==========================================
# 2. CREATE MUSYRIF
# ==========================================
class MusyrifCreate(BaseModel):
    username: str
    password: str
    nama_lengkap: str

@router.post("/musyrif")
async def create_musyrif(data: MusyrifCreate, session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    cek_user = session.exec(select(User).where(User.username == data.username)).first()
    if cek_user:
        raise HTTPException(status_code=400, detail="Username musyrif sudah digunakan!")
    
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
# 3. GET ALL MUSYRIF
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
        raise HTTPException(status_code=500, detail=f"Gagal mengambil data musyrif: {str(e)}")


# ==========================================
# 4. KELOMPOK HALAQAH (CREATE & GET)
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
# 5. SANTRI MANAGEMENT
# ==========================================
class SantriCreate(BaseModel):
    nama_santri: str
    nomor_induk: str
    kelompok_id: Optional[int] = None

@router.post("/santri")
def create_santri(data: SantriCreate, session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    if data.kelompok_id: 
        kelompok = session.get(KelompokHalaqah, data.kelompok_id)
        if not kelompok:
            raise HTTPException(status_code=404, detail="Kelompok halaqah tidak ditemukan!")
    
    santri = Santri(
        nama_santri=data.nama_santri,
        nomor_induk=data.nomor_induk,
        kelompok_id=data.kelompok_id
    )
    session.add(santri)
    session.commit()
    session.refresh(santri)
    return {"status": "success", "data": santri}


@router.get("/santri")
async def get_semua_santri(session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    try:
        statement = (
            select(
                Santri, 
                KelompokHalaqah, 
                User, 
                func.count(SetoranTahfizh.id).label("total_setoran")
            )
            .join(KelompokHalaqah, Santri.kelompok_id == KelompokHalaqah.id, isouter=True)
            .join(User, KelompokHalaqah.musyrif_id == User.id, isouter=True)
            .join(SetoranTahfizh, Santri.id == SetoranTahfizh.santri_id, isouter=True)
            .group_by(Santri.id, KelompokHalaqah.id, User.id)
        )
        results = session.exec(statement).all()
        
        output = []
        for santri, kelompok, musyrif, total_setoran in results:
            output.append({
                "id": santri.id,
                "nama_santri": santri.nama_santri,
                "nomor_induk": santri.nomor_induk,
                "status_santri": getattr(santri, 'status_santri', 'hadir') or 'hadir',
                "target_semester": getattr(santri, 'target_semester', None),
                "target_harian": getattr(santri, 'target_harian', None),
                "kelompok_id": santri.kelompok_id,
                "nama_kelompok": kelompok.nama_kelompok if kelompok else "Belum Masuk Kelompok",
                "nama_ustadz": musyrif.nama_lengkap if musyrif else "Ustadz Belum Diplotting",
                "total_setoran": total_setoran
            })
            
        return {"status": "success", "data": output}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal memuat list data santri: {str(e)}")


class SantriUpdate(BaseModel):
    student_id: Optional[int] = None
    nama_santri: Optional[str] = None
    nomor_induk: Optional[str] = None
    kelompok_id: Optional[int] = None

@router.put("/santri/{santri_id}")
def update_santri_plotting(
    santri_id: int,
    data: SantriUpdate,
    session: Session = Depends(get_session),
    admin: User = Depends(get_current_admin)
):
    santri = session.exec(select(Santri).where(Santri.id == santri_id)).first()
    if not santri:
        raise HTTPException(status_code=404, detail=f"Santri dengan ID {santri_id} tidak ditemukan!")
    
    if data.nama_santri: santri.nama_santri = data.nama_santri
    if data.nomor_induk: santri.nomor_induk = data.nomor_induk
    if data.kelompok_id is not None: santri.kelompok_id = data.kelompok_id
    
    session.add(santri)
    session.commit()
    session.refresh(santri)
    return {"status": "success", "data": santri}


@router.delete("/santri/{santri_id}") 
def delete_santri(santri_id: int, session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    santri = session.exec(select(Santri).where(Santri.id == santri_id)).first()
    if not santri:
        raise HTTPException(status_code=404, detail="Data santri tidak ditemukan")
    
    session.exec(delete(SetoranTahfizh).where(SetoranTahfizh.santri_id == santri_id))
    session.delete(santri)
    session.commit()
    return {"status": "success", "message": "Data santri berhasil dihapus!"}


# ==========================================
# 6. ADMIN FITUR BARU: INPUT TARGET SEMESTER
# ==========================================
class TargetAdminPayload(BaseModel):
    target_semester: str

@router.put("/santri/{santri_id}/target-semester")
def set_target_semester(
    santri_id: int, 
    payload: TargetAdminPayload, 
    session: Session = Depends(get_session),
    admin: User = Depends(get_current_admin)
):
    santri = session.get(Santri, santri_id)
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan!")

    santri.target_semester = payload.target_semester
    session.add(santri)
    session.commit()
    session.refresh(santri)
    
    return {
        "status": "success", 
        "message": f"Target semester untuk {santri.nama_santri} berhasil disimpan!",
        "target_semester": santri.target_semester
    }


# ==========================================
# 7. MANAJEMEN USER / MUSYRIF
# ==========================================
class UpdateRolePayload(BaseModel):
    role: str

@router.put("/musyrif/{user_id}")
def change_musyrif_role(user_id: int, payload: UpdateRolePayload, session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Musyrif/User tidak ditemukan!")
    
    if payload.role not in ["admin", "musyrif"]:
        raise HTTPException(status_code=400, detail="Role harus 'admin' atau 'musyrif'!")

    user.role = payload.role
    session.add(user)
    session.commit()
    session.refresh(user)
    
    return {
        "status": "success", 
        "message": f"Berhasil mengubah role {user.nama_lengkap} menjadi {payload.role.upper()}!",
        "data": {"id": user.id, "username": user.username, "role": user.role}
    }


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
# 8. DISRUPSI JADWAL HALAQAH & MONITORING
# ==========================================
class CatatDisrupsiRequest(BaseModel):
    kelompok_id: Optional[int] = None
    badal_musyrif_id: Optional[int] = None
    alasan: str
    status_halaqah: str

@router.post("/halaqah/disrupsi")
def catat_disrupsi_halaqah(data: CatatDisrupsiRequest, session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    
    # 1. LOGIC JIKA DILIBURKAN TOTAL (BERLAKU UNTUK SEMUA)
    if data.status_halaqah == "diliburkan_total":
        kelompok_list = session.exec(select(KelompokHalaqah)).all()
        if not kelompok_list:
            raise HTTPException(status_code=400, detail="Belum ada kelompok halaqah yang terdaftar!")
        
        for k in kelompok_list:
            log_libur = HalaqahDisruption(
                kelompok_id=k.id,
                musyrif_id=k.musyrif_id,
                badal_musyrif_id=None,
                alasan=data.alasan,
                status_halaqah="diliburkan_total"
            )
            session.add(log_libur)
        session.commit()
        return {"status": "success", "message": "Semua halaqah berhasil diliburkan!"}

    # 2. LOGIC JIKA DIGANTI BADAL (BERLAKU UNTUK 1 KELOMPOK)
    if not data.kelompok_id:
        raise HTTPException(status_code=400, detail="Kelompok ID wajib diisi untuk ustadz badal!")
        
    kelompok = session.get(KelompokHalaqah, data.kelompok_id)
    if not kelompok:
        raise HTTPException(status_code=404, detail="Kelompok halaqah tidak ditemukan!")
        
    if data.badal_musyrif_id:
        badal = session.get(User, data.badal_musyrif_id)
        if not badal or badal.role != "musyrif":
            raise HTTPException(status_code=400, detail="User pengganti tidak valid atau bukan musyrif!")
        if badal.id == kelompok.musyrif_id:
            raise HTTPException(status_code=400, detail="Musyrif asli tidak bisa menjadi badal!")

    log_gangguan = HalaqahDisruption(
        kelompok_id=data.kelompok_id,
        musyrif_id=kelompok.musyrif_id,
        badal_musyrif_id=data.badal_musyrif_id,
        alasan=data.alasan,
        status_halaqah=data.status_halaqah
    )
    session.add(log_gangguan)
    session.commit()
    return {"status": "success", "message": "Disrupsi badal tercatat!"}


@router.get("/halaqah/monitor")
def get_monitor_halaqah(session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    try:
        kelompok_list = session.exec(select(KelompokHalaqah)).all()
        hasil = []
        
        for k in kelompok_list:
            musyrif_asli = session.get(User, k.musyrif_id)
            badal_aktif = session.exec(
                select(HalaqahDisruption)
                .where(
                    HalaqahDisruption.kelompok_id == k.id, 
                    HalaqahDisruption.status_halaqah.in_(["diganti_badal", "diliburkan_total"])
                )
                .order_by(HalaqahDisruption.id.desc())
            ).first()
            
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
                "status_halaqah": badal_aktif.status_halaqah if badal_aktif else "normal",
                "info_badal": {
                    "nama_badal": nama_badal,
                    "alasan": badal_aktif.alasan if badal_aktif else ""
                } if badal_aktif else None
            })
            
        return {"status": "success", "data": hasil}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal memuat data monitor halaqah: {str(e)}")


@router.put("/halaqah/{kelompok_id}/cancel-badal")
def cancel_badal_halaqah(kelompok_id: int, session: Session = Depends(get_session), admin: User = Depends(get_current_admin)):
    statement = select(HalaqahDisruption).where(
        HalaqahDisruption.kelompok_id == kelompok_id,
        HalaqahDisruption.status_halaqah.in_(["diganti_badal", "diliburkan_total"])
    ).order_by(HalaqahDisruption.id.desc())
    
    disrupsi = session.exec(statement).first()
    if not disrupsi:
        raise HTTPException(status_code=400, detail="Halaqah ini tidak sedang di-badal!")
        
    disrupsi.status_halaqah = "selesai"
    session.add(disrupsi)
    session.commit()
    
    return {
        "status": "success", 
        "message": "Status badal berhasil dibatalkan! Halaqah kembali dipegang musyrif asli."
    }


# ==========================================
# 9. HISTORI LAPORAN PER HALAQAH
# ==========================================
@router.get("/halaqah/{kelompok_id}/laporan")
def get_laporan_per_halaqah(
    kelompok_id: int, 
    session: Session = Depends(get_session), 
    admin: User = Depends(get_current_admin)
):
    try:
        statement = (
            select(SetoranTahfizh, Santri)
            .join(Santri, SetoranTahfizh.santri_id == Santri.id)
            .where(Santri.kelompok_id == kelompok_id)
            .order_by(SetoranTahfizh.id.desc())
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
        raise HTTPException(status_code=500, detail=f"Gagal mengambil histori laporan: {str(e)}")


# ==========================================
# 10. GET PROFILE USER / MUSYRIF
# ==========================================
@router.get("/musyrif/{user_id}/profile")
def get_profile_musyrif(
    user_id: int, 
    session: Session = Depends(get_session), 
    admin: User = Depends(get_current_admin)
):
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User / Musyrif tidak ditemukan")

    kelompok_list = session.exec(
        select(KelompokHalaqah).where(KelompokHalaqah.musyrif_id == user.id)
    ).all()

    kelompok_ids = [k.id for k in kelompok_list]

    total_santri = 0
    if kelompok_ids:
        total_santri = session.exec(
            select(func.count(Santri.id)).where(Santri.kelompok_id.in_(kelompok_ids))
        ).one()

    histori_setoran = []
    if kelompok_ids:
        statement = (
            select(SetoranTahfizh, Santri)
            .join(Santri, SetoranTahfizh.santri_id == Santri.id)
            .where(Santri.kelompok_id.in_(kelompok_ids))
            .order_by(SetoranTahfizh.id.desc())
        )
        results = session.exec(statement).all()
        for s, santri in results:
            histori_setoran.append({
                "id_setoran": s.id,
                "santri_id": santri.id,
                "nama_santri": santri.nama_santri,
                "surah": s.surah,
                "ayat": s.ayat,
                "status_kelancaran": s.status_kelancaran,
                "catatan_musyrif": getattr(s, 'catatan_musyrif', ''),
                "waktu_setoran": format_indonesia(getattr(s, 'created_at', None))
            })

    return {
        "status": "success",
        "profile": {
            "id": user.id,
            "username": user.username,
            "nama_lengkap": user.nama_lengkap,
            "role": user.role,
            "tanggal_dibuat": format_indonesia(getattr(user, "created_at", None), "%d/%m/%Y") if getattr(user, "created_at", None) else "-",
            "total_kelompok": len(kelompok_list),
            "kelompok_diampu": [k.nama_kelompok for k in kelompok_list],
            "total_santri_binaan": total_santri,
            "total_setoran_diampu": len(histori_setoran)
        },
        "histori_setoran_full": histori_setoran
    }