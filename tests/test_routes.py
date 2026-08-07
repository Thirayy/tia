import os
from datetime import date

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_tia.db")
os.environ.setdefault("COOKIE_SECURE", "false")

from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from app.database import engine
from app.models import KelompokHalaqah, Santri, SetoranTahfizh, User
from app.security import is_password_hash
from main import app


def reset_db():
    with Session(engine) as session:
        session.exec(delete(SetoranTahfizh))
        session.exec(delete(Santri))
        session.exec(delete(KelompokHalaqah))
        session.exec(delete(User))
        session.commit()


def add_user(username: str, password_hash: str, role: str = "admin") -> User:
    with Session(engine) as session:
        user = User(
            username=username,
            password_hash=password_hash,
            nama_lengkap=username.title(),
            role=role,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def test_login_route_hashes_legacy_plaintext_password():
    with TestClient(app) as client:
        reset_db()
        add_user("admin", "admin123")

        response = client.post("/auth/login", json={"username": "admin", "password": "admin123"})

        assert response.status_code == 200
        with Session(engine) as session:
            user = session.exec(select(User).where(User.username == "admin")).one()
            assert is_password_hash(user.password_hash)


def test_admin_routes_require_admin_session():
    with TestClient(app) as client:
        reset_db()
        add_user("musyrif", "pw", role="musyrif")

        response = client.get("/admin/santri", headers={"x-session-user": "musyrif"})

        assert response.status_code == 403


def test_docs_available_without_api_prefix():
    with TestClient(app) as client:
        response = client.get("/docs")

        assert response.status_code == 200


def test_admin_can_manage_pending_exam_list():
    with TestClient(app) as client:
        reset_db()
        admin = add_user("admin", "admin123")
        musyrif = add_user("musyrif", "pw", role="musyrif")

        with Session(engine) as session:
            kelompok = KelompokHalaqah(nama_kelompok="Halaqah 1", musyrif_id=musyrif.id)
            session.add(kelompok)
            session.commit()
            session.refresh(kelompok)

            santri = Santri(
                nama_santri="Ali",
                nomor_induk="S001",
                kelompok_id=kelompok.id,
                status_santri="persiapan_ujian",
                tanggal_ujian=date(2026, 8, 6),
            )
            session.add(santri)
            session.commit()
            session.refresh(santri)

        list_response = client.get(
            "/admin/ujian/santri",
            headers={"x-session-user": admin.username},
        )

        assert list_response.status_code == 200
        payload = list_response.json()
        assert payload["status"] == "success"
        assert payload["data"][0]["nama_santri"] == "Ali"

        finish_response = client.post(
            f"/admin/ujian/santri/{santri.id}/hasil",
            headers={"x-session-user": admin.username},
            json={"hasil": "lulus", "catatan": "Selesai"},
        )

        assert finish_response.status_code == 200

        refreshed = client.get(
            "/admin/ujian/santri",
            headers={"x-session-user": admin.username},
        )
        assert refreshed.json()["data"] == []


def test_musyrif_can_upload_santri_profile_photo():
    with TestClient(app) as client:
        reset_db()
        musyrif = add_user("musyrif", "pw", role="musyrif")

        with Session(engine) as session:
            kelompok = KelompokHalaqah(nama_kelompok="Halaqah 1", musyrif_id=musyrif.id)
            session.add(kelompok)
            session.commit()
            session.refresh(kelompok)

            santri = Santri(
                nama_santri="Ali",
                nomor_induk="S001",
                kelompok_id=kelompok.id,
            )
            session.add(santri)
            session.commit()
            session.refresh(santri)

        response = client.post(
            f"/musyrif/santri/{santri.id}/upload-foto",
            headers={"x-session-user": musyrif.username},
            files={"file": ("avatar.png", b"fake-image-bytes", "image/png")},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["foto_profile"].startswith("/static/uploads/profiles/")

        with Session(engine) as session:
            stored_santri = session.get(Santri, santri.id)
            assert stored_santri.foto_profile.startswith("/static/uploads/profiles/")
