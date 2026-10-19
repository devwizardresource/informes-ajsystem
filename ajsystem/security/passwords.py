"""Hash de contraseñas con Argon2id (argon2-cffi, parámetros recomendados RFC 9106 perfil bajo-memoria)."""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # Argon2id, t=3, m=64 MiB, p=4 (valores por defecto de la biblioteca)
# Hash ficticio para igualar el tiempo de respuesta cuando el usuario no existe.
_DUMMY_HASH = _hasher.hash("contraseña-ficticia-para-tiempo-constante")

MIN_LENGTH = 12
MAX_LENGTH = 256


def validate_policy(password: str, username: str = "") -> list[str]:
    errors = []
    if len(password) < MIN_LENGTH:
        errors.append(f"La contraseña debe tener al menos {MIN_LENGTH} caracteres.")
    if len(password) > MAX_LENGTH:
        errors.append("La contraseña es demasiado larga.")
    if username and username.lower() in password.lower():
        errors.append("La contraseña no puede contener el nombre de usuario.")
    if password.strip() != password:
        errors.append("La contraseña no puede empezar ni terminar con espacios.")
    return errors


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)
