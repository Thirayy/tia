from hmac import compare_digest

from passlib.context import CryptContext


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, stored_password: str) -> bool:
    if stored_password.startswith("$2"):
        return pwd_context.verify(password, stored_password)
    return compare_digest(password, stored_password)


def is_password_hash(stored_password: str) -> bool:
    return stored_password.startswith("$2")
