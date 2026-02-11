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
_BOOT_NONCE = secrets.token_hex(16)
JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32)) + _BOOT_NONCE
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


# ─── TOTP 2FA (multi-device) ───

def _generate_totp_secret(device_name: str = "Authenticator") -> tuple[str, str]:
    """Generate a new TOTP secret and return (secret, otpauth_uri)"""
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    label = f"admin ({device_name})" if device_name else "admin"
    uri = totp.provisioning_uri(name=label, issuer_name=APP_NAME)
    return secret, uri


def _get_totp_qr_png(uri: str) -> str:
    """Generate QR code as base64 PNG"""
    qr = qrcode.QRCode(version=1, box_size=6, border=2)
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


def setup_2fa(device_name: str = "Authenticator") -> dict:
    """Start 2FA setup for a new device. Returns QR + secret + pending device id."""
    secret, uri = _generate_totp_secret(device_name)
    device_id = secrets.token_hex(8)
    auth = _load_auth()
    _migrate_totp(auth)
    # Store pending device (not yet verified)
    auth["_pending_device"] = {
        "id": device_id,
        "name": device_name,
        "secret": secret,
    }
    _save_auth(auth)
    qr_data = _get_totp_qr_png(uri)
    return {
        "device_id": device_id,
        "secret": secret,
        "uri": uri,
        "qr": qr_data,
    }


def verify_2fa_setup(code: str) -> Optional[dict]:
    """Verify the TOTP code during setup. Returns the device dict on success, None on failure."""
    auth = _load_auth()
    _migrate_totp(auth)
    pending = auth.get("_pending_device")
    if not pending:
        return None
    totp = pyotp.TOTP(pending["secret"])
    if totp.verify(code, valid_window=1):
        device = {
            "id": pending["id"],
            "name": pending["name"],
            "secret": pending["secret"],
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
        devices = auth.get("totp_devices", [])
        devices.append(device)
        auth["totp_devices"] = devices
        auth.pop("_pending_device", None)
        _save_auth(auth)
        return {"id": device["id"], "name": device["name"], "added_at": device["added_at"]}
    return None


def verify_totp(code: str) -> bool:
    """Verify TOTP code during login — tries all registered devices."""
    auth = _load_auth()
    _migrate_totp(auth)
    devices = auth.get("totp_devices", [])
    if not devices:
        return True  # 2FA not enabled, skip
    for dev in devices:
        totp = pyotp.TOTP(dev["secret"])
        if totp.verify(code, valid_window=1):
            return True
    return False


def list_devices() -> list[dict]:
    """List all registered 2FA devices (without secrets)."""
    auth = _load_auth()
    _migrate_totp(auth)
    return [
        {"id": d["id"], "name": d["name"], "added_at": d.get("added_at", "")}
        for d in auth.get("totp_devices", [])
    ]


def remove_device(device_id: str) -> bool:
    """Remove a 2FA device by id. Returns True if found and removed."""
    auth = _load_auth()
    _migrate_totp(auth)
    devices = auth.get("totp_devices", [])
    new_devices = [d for d in devices if d["id"] != device_id]
    if len(new_devices) == len(devices):
        return False
    auth["totp_devices"] = new_devices
    _save_auth(auth)
    return True


def disable_2fa() -> bool:
    """Remove all 2FA devices."""
    auth = _load_auth()
    auth.pop("totp_secret", None)
    auth.pop("totp_verified", None)
    auth.pop("_pending_device", None)
    auth["totp_devices"] = []
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
