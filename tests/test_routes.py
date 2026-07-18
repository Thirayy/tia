import os

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
