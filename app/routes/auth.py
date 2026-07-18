import os
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from sqlmodel import Session, select
from pydantic import BaseModel
from app.database import get_session
from app.models import User

COOKIE_DOMAIN = os.getenv("COOKIE_DOMAIN", "tia.khwarizmi.co.id")

router = APIRouter()

class LoginRequest(BaseModel):
    username: str
    password: str

# ==========================================================
# 1. ENDPOINT LOGIN (Set Cookie)
# ==========================================================
@router.post("/login")
async def login(data: LoginRequest, response: Response, session: Session = Depends(get_session)):
    # Cari user berdasarkan username
    user = session.query(User).filter(User.username == data.username).first()
    
    # Validasi user dan password
    if not user or user.password_hash != data.password:
        raise HTTPException(status_code=401, detail="Username atau password salah!")

    # Set Session Cookie
    response.set_cookie(
        key="session_user",
        value=user.username,
        httponly=True,
        samesite="lax",
        secure=True, 
        domain=COOKIE_DOMAIN,
        max_age=86400
    )
    
    # Return respons sukses
    return {
        "status": "success",
        "message": f"Selamat datang, {user.nama_lengkap}!",
        "role": user.role,
        "user": {
            "id": user.id,
            "username": user.username,
            "nama_lengkap": user.nama_lengkap,
            "role": user.role
        }
    }

# ==========================================
# 2. ENDPOINT LOGOUT (Hapus Cookie)
# ==========================================
@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(key="session_user")
    return {"status": "success", "message": "Berhasil logout!"}

# ==========================================
# 3. ENDPOINT CEK STATUS
# ==========================================
@router.get("/status")
async def check_status(request: Request, session: Session = Depends(get_session)):
    username = request.cookies.get("session_user")
    if not username:
        raise HTTPException(status_code=401, detail="Belum login, cookie gak ketemu.")
    
    statement = select(User).where(User.username == username)
    user = session.exec(statement).first()
    
    if not user:
        raise HTTPException(status_code=401, detail="Session invalid.")
        
    return {
        "status": "authenticated",
        "username": user.username,
        "nama_lengkap": user.nama_lengkap,
        "role": user.role
    }
