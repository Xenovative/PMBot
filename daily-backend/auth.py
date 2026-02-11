"""
Authentication module: password + TOTP 2FA + JWT tokens
"""
import os
import json
import time
import hashlib
import hmac
import secrets
import base64
from typing import Optional
from datetime import datetime, timezone

import pyotp
import qrcode
import qrcode.image.svg
from io import BytesIO

from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# ─── Config ───
AUTH_FILE = os.path.join(os.path.dirname(__file__), ".auth.json")
JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))
JWT_EXPIRY_HOURS = int(os.environ.get("JWT_EXPIRY_HOURS", "24"))
APP_NAME = "PMBot Daily"

security = HTTPBearer(auto_error=False)


# ─── Simple JWT (no pyjwt dependency) ───

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    s += "=" * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s)


def _jwt_sign(payload: dict) -> str:
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64url_encode(json.dumps(payload).encode())
    sig_input = f"{header}.{body}".encode()
    sig = hmac.new(JWT_SECRET.encode(), sig_input, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64url_encode(sig)}"


def _jwt_verify(token: str) -> Optional[dict]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        sig_input = f"{parts[0]}.{parts[1]}".encode()
        expected_sig = hmac.new(JWT_SECRET.encode(), sig_input, hashlib.sha256).digest()
        actual_sig = _b64url_decode(parts[2])
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
        payload = json.loads(_b64url_decode(parts[1]))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def create_token(subject: str = "admin") -> str:
    payload = {
        "sub": subject,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXPIRY_HOURS * 3600,
    }
    return _jwt_sign(payload)


# ─── Password hashing (SHA-256 + salt, no bcrypt dependency) ───

def _hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return hashed.hex(), salt


def _verify_password(password: str, stored_hash: str, salt: str) -> bool:
    computed, _ = _hash_password(password, salt)
    return hmac.compare_digest(computed, stored_hash)


# ─── Auth state persistence ───

def _load_auth() -> dict:
    if os.path.exists(AUTH_FILE):
        with open(AUTH_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_auth(data: dict):
    with open(AUTH_FILE, "w") as f:
        json.dump(data, f, indent=2)


def is_setup_complete() -> bool:
    auth = _load_auth()
    return bool(auth.get("password_hash"))


def is_2fa_enabled() -> bool:
    auth = _load_auth()
    return bool(auth.get("totp_secret") and auth.get("totp_verified"))


# ─── Setup: initial password ───

def setup_password(password: str):
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    auth = _load_auth()
    hashed, salt = _hash_password(password)
    auth["password_hash"] = hashed
    auth["password_salt"] = salt
    _save_auth(auth)


def verify_password(password: str) -> bool:
    auth = _load_auth()
    stored_hash = auth.get("password_hash")
    salt = auth.get("password_salt")
    if not stored_hash or not salt:
        return False
    return _verify_password(password, stored_hash, salt)


# ─── TOTP 2FA ───

def generate_totp_secret() -> tuple[str, str]:
    """Generate a new TOTP secret and return (secret, otpauth_uri)"""
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name="admin", issuer_name=APP_NAME)
    return secret, uri


def get_totp_qr_svg(uri: str) -> str:
    """Generate QR code as base64 PNG"""
    qr = qrcode.QRCode(version=1, box_size=6, border=2)
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="white", back_color="transparent")
    buf = BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


def setup_2fa() -> dict:
    """Start 2FA setup: generate secret, return QR + secret for user"""
    secret, uri = generate_totp_secret()
    auth = _load_auth()
    auth["totp_secret"] = secret
    auth["totp_verified"] = False
    _save_auth(auth)
    qr_data = get_totp_qr_svg(uri)
    return {
        "secret": secret,
        "uri": uri,
        "qr": qr_data,
    }


def verify_2fa_setup(code: str) -> bool:
    """Verify the TOTP code during setup to confirm user has the authenticator"""
    auth = _load_auth()
    secret = auth.get("totp_secret")
    if not secret:
        return False
    totp = pyotp.TOTP(secret)
    if totp.verify(code, valid_window=1):
        auth["totp_verified"] = True
        _save_auth(auth)
        return True
    return False


def verify_totp(code: str) -> bool:
    """Verify TOTP code during login"""
    auth = _load_auth()
    secret = auth.get("totp_secret")
    if not secret or not auth.get("totp_verified"):
        return True  # 2FA not enabled, skip
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


def disable_2fa() -> bool:
    auth = _load_auth()
    auth.pop("totp_secret", None)
    auth.pop("totp_verified", None)
    _save_auth(auth)
    return True


# ─── FastAPI dependency ───

async def require_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    """Dependency: require valid JWT. Skip if no password is set yet (first-time setup)."""
    if not is_setup_complete():
        return {"sub": "setup"}

    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authorization token")

    payload = _jwt_verify(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return payload


async def optional_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    """Dependency: returns payload if valid token, None otherwise."""
    if not credentials:
        return None
    return _jwt_verify(credentials.credentials)
