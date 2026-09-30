"""Usuarios, contraseñas, sesiones y permisos por rol."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

ROLES = {
    "admin": "Administrador",
    "analista": "Analista",
    "operador": "Operador VAR",
    "arbitro": "Árbitro",
}

# Qué puede hacer cada rol.
PERMISSIONS = {
    "predict": {"admin", "analista", "operador", "arbitro"},  # consultar probabilidad
    "decide": {"admin", "operador", "arbitro"},  # registrar la decisión final
    "view": {"admin", "analista", "operador", "arbitro"},  # historial y métricas
    "manage_data": {"admin", "analista"},  # árbitros, importar, recalibrar
    "manage_users": {"admin"},
}

SESSION_HOURS = 12
MIN_PASSWORD_LENGTH = 8
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 300

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def can(role: str, permission: str) -> bool:
    return role in PERMISSIONS.get(permission, set())


def permissions_for(role: str) -> list[str]:
    return sorted(p for p, roles in PERMISSIONS.items() if role in roles)


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Hash válido pero de una contraseña aleatoria: se usa para gastar el mismo
# tiempo cuando el usuario no existe y no revelar qué usuarios hay.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_expiry() -> str:
    # Mismo formato que datetime('now') de SQLite, para poder comparar como texto.
    return (datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)).strftime("%Y-%m-%d %H:%M:%S")


def generate_password() -> str:
    return secrets.token_urlsafe(12)


class LoginThrottle:
    """Bloquea temporalmente un usuario tras varios intentos fallidos seguidos."""

    def __init__(self, max_failures: int = MAX_FAILED_LOGINS, lockout: float = LOCKOUT_SECONDS):
        self.max_failures = max_failures
        self.lockout = lockout
        self._state: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def locked(self, username: str) -> bool:
        with self._lock:
            _, until = self._state.get(username.lower(), (0, 0.0))
            return until > time.monotonic()

    def failure(self, username: str) -> None:
        key = username.lower()
        with self._lock:
            fails, _ = self._state.get(key, (0, 0.0))
            fails += 1
            until = time.monotonic() + self.lockout if fails >= self.max_failures else 0.0
            self._state[key] = (0 if until else fails, until)

    def success(self, username: str) -> None:
        with self._lock:
            self._state.pop(username.lower(), None)
