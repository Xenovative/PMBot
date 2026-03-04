"""
Authentication module: password + TOTP 2FA + JWT tokens
Ported from daily/m5 backends.
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

# ─── Login rate limiting ───
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300  # 5 minutes
LOGIN_LOCKOUT_SECONDS = 900  # 15 minutes after exceeding max
_login_attempts: dict[str, list[float]] = {}  # ip -> [timestamps]


def _check_rate_limit(ip: str) -> Optional[int]:
    """Check if IP is rate-limited. Returns seconds until unlock, or None if OK."""
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Prune old attempts outside the lockout window
    attempts = [t for t in attempts if now - t < LOGIN_LOCKOUT_SECONDS]
    _login_attempts[ip] = attempts

    if len(attempts) >= LOGIN_MAX_ATTEMPTS:
        oldest_excess = attempts[-LOGIN_MAX_ATTEMPTS]
        unlock_at = oldest_excess + LOGIN_LOCKOUT_SECONDS
        if now < unlock_at:
            return int(unlock_at - now)
    return None


def _record_login_attempt(ip: str):
    """Record a failed login attempt."""
    _login_attempts.setdefault(ip, []).append(time.time())


def _clear_login_attempts(ip: str):
    """Clear attempts on successful login."""
    _login_attempts.pop(ip, None)


_BOOT_NONCE = secrets.token_hex(16)
JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32)) + _BOOT_NONCE
JWT_EXPIRY_HOURS = int(os.environ.get("JWT_EXPIRY_HOURS", "24"))
APP_NAME = "PMBot"

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

        # Reject empty signature (alg:none attack)
        if not parts[2]:
            return None

        # Validate header — only accept HS256
        header = json.loads(_b64url_decode(parts[0]))
        if header.get("alg") != "HS256":
            return None

        sig_input = f"{parts[0]}.{parts[1]}".encode()
        expected_sig = hmac.new(JWT_SECRET.encode(), sig_input, hashlib.sha256).digest()
        actual_sig = _b64url_decode(parts[2])
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        payload = json.loads(_b64url_decode(parts[1]))

        # Require exp claim and reject expired tokens
        exp = payload.get("exp")
        if exp is None or exp < time.time():
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


def _migrate_totp(auth_data: dict) -> bool:
    """Migrate old single totp_secret to totp_devices list. Returns True if migrated."""
    if "totp_secret" in auth_data and "totp_devices" not in auth_data:
        old_secret = auth_data.pop("totp_secret")
        old_verified = auth_data.pop("totp_verified", False)
        if old_secret and old_verified:
            auth_data["totp_devices"] = [{
                "id": secrets.token_hex(8),
                "name": "Authenticator",
                "secret": old_secret,
                "added_at": datetime.now(timezone.utc).isoformat(),
            }]
        else:
            auth_data.pop("totp_secret", None)
            auth_data.pop("totp_verified", None)
            auth_data["totp_devices"] = []
        return True
    return False


def is_2fa_enabled() -> bool:
    auth = _load_auth()
    if _migrate_totp(auth):
        _save_auth(auth)
    return len(auth.get("totp_devices", [])) > 0


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


# ─── TOTP handling ───

def setup_2fa(device_name: str = "Authenticator") -> dict:
    auth = _load_auth()
    if _migrate_totp(auth):
        _save_auth(auth)
    secret = pyotp.random_base32()
    provisioning_uri = pyotp.totp.TOTP(secret).provisioning_uri(name="admin", issuer_name=APP_NAME)

    # Generate QR code SVG
    img = qrcode.make(provisioning_uri, image_factory=qrcode.image.svg.SvgImage)
    buffer = BytesIO()
    img.save(buffer)
    svg_data = buffer.getvalue().decode()

    return {
        "secret": secret,
        "provisioning_uri": provisioning_uri,
        "qr_svg": svg_data,
    }


def verify_2fa_setup(code: str) -> Optional[dict]:
    auth = _load_auth()
    temp_secret = None
    # Allow verifying against any existing temp secret
    # (frontends should pass the secret they received, but not stored server-side)
    # To keep compatibility, allow verifying against the most recently issued secret
    temp_secret = auth.get("temp_totp_secret")
    secrets_to_check = [temp_secret] if temp_secret else []
    for secret in secrets_to_check:
        totp = pyotp.TOTP(secret)
        if totp.verify(code, valid_window=1):
            device = {
                "id": secrets.token_hex(8),
                "name": "Authenticator",
                "secret": secret,
                "added_at": datetime.now(timezone.utc).isoformat(),
            }
            devices = auth.get("totp_devices", [])
            devices.append(device)
            auth["totp_devices"] = devices
            auth.pop("temp_totp_secret", None)
            _save_auth(auth)
            return device
    return None


def verify_totp(code: str) -> bool:
    auth = _load_auth()
    if _migrate_totp(auth):
        _save_auth(auth)
    devices = auth.get("totp_devices", [])
    for device in devices:
        totp = pyotp.TOTP(device.get("secret"))
        if totp.verify(code, valid_window=1):
            return True
    return False


def list_devices() -> list:
    auth = _load_auth()
    if _migrate_totp(auth):
        _save_auth(auth)
    return auth.get("totp_devices", [])


def remove_device(device_id: str) -> bool:
    auth = _load_auth()
    devices = auth.get("totp_devices", [])
    new_devices = [d for d in devices if d.get("id") != device_id]
    if len(new_devices) != len(devices):
        auth["totp_devices"] = new_devices
        _save_auth(auth)
        return True
    return False


# ─── Auth dependency ───

def require_auth(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing token")
    token = credentials.credentials
    payload = _jwt_verify(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload


def require_auth_ws(token: Optional[str]):
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    payload = _jwt_verify(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload


# ─── Optional: helper to attach auth info to request (unused currently) ───

def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    return require_auth(credentials)
