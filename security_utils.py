"""
security_utils.py
-----------------
Thin security layer for the TCC Display System.

Provides:
  - audit_log()     : write structured records to logs/audit.log
  - sign_action()   : HMAC-SHA256 signature for scheduled/API actions
  - verify_action() : verify a signature produced by sign_action()
  - encrypt_text()  : Fernet (AES-128-CBC + HMAC) symmetric encryption
  - decrypt_text()  : corresponding decryption

All functions are stateless and import-safe (no Flask context required).
"""

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
LOG_DIR  = os.path.join(os.path.dirname(__file__), 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'audit.log')
MAX_LOG_BYTES = 10 * 1024 * 1024   


def _rotate_if_needed():
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > MAX_LOG_BYTES:
        ts  = datetime.now().strftime('%Y%m%d_%H%M%S')
        dst = LOG_FILE + '.' + ts
        try:
            os.rename(LOG_FILE, dst)
        except Exception:
            pass


def audit_log(event_type: str,
              description: str,
              user: str = 'system',
              target: str = '',
              status: str = 'success',
              extra: dict | None = None) -> None:
    """
    Append a JSON-lines record to logs/audit.log.

    Parameters
    ----------
    event_type  : uppercase category string, e.g. 'LOGIN', 'ALERT_SEND'
    description : human-readable description of what happened
    user        : username or 'system'
    target      : object affected (recipient, file, relay channel, …)
    status      : 'success' | 'failure' | 'warning'
    extra       : optional dict with additional fields
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    _rotate_if_needed()

    record = {
        'ts':          datetime.now(timezone.utc).isoformat(),
        'event_type':  event_type,
        'description': description,
        'user':        user,
        'target':      target,
        'status':      status,
    }
    if extra:
        record.update(extra)

    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception as e:
        
        print(f'[audit_log ERROR] {e} | record={record}')


# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #

_HMAC_KEY = (os.environ.get('SECRET_KEY') or 'temporary-development-key').encode()


def sign_action(payload: dict, expires_in: int = 300) -> str:
    """
    Return an HMAC-SHA256 hex digest that covers payload + expiry timestamp.

    Parameters
    ----------
    payload    : dict describing the action (will be JSON-serialised)
    expires_in : seconds until the token expires (default 5 min)

    Returns
    -------
    'expiry_ts.hex_signature'
    """
    expiry = int(time.time()) + expires_in
    message = json.dumps({'payload': payload, 'exp': expiry},
                         sort_keys=True, ensure_ascii=False).encode()
    sig = hmac.new(_HMAC_KEY, message, hashlib.sha256).hexdigest()
    return f'{expiry}.{sig}'


def verify_action(payload: dict, token: str) -> bool:
    """
    Verify a token produced by sign_action().

    Returns True only if:
    - token is well-formed
    - HMAC matches (constant-time compare)
    - token has not expired
    """
    try:
        expiry_str, given_sig = token.split('.', 1)
        expiry = int(expiry_str)
    except (ValueError, AttributeError):
        return False

    if time.time() > expiry:
        return False

    message = json.dumps({'payload': payload, 'exp': expiry},
                         sort_keys=True, ensure_ascii=False).encode()
    expected_sig = hmac.new(_HMAC_KEY, message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_sig, given_sig)


# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
def _get_fernet():
    """Lazy-import cryptography so the module loads even without the package."""
    try:
        from cryptography.fernet import Fernet
        return Fernet
    except ImportError:
        return None


def _get_or_create_key() -> bytes:
    """
    Load or create the Fernet key stored at logs/.fernet_key.
    Key file is created with mode 600 (owner-read-only on Unix).
    """
    key_path = os.path.join(LOG_DIR, '.fernet_key')
    os.makedirs(LOG_DIR, exist_ok=True)

    if os.path.exists(key_path):
        with open(key_path, 'rb') as fh:
            return fh.read().strip()

    try:
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
    except ImportError:
        
        import base64
        raw = hashlib.sha256(_HMAC_KEY).digest()
        key = base64.urlsafe_b64encode(raw)

    with open(key_path, 'wb') as fh:
        fh.write(key)
    try:
        os.chmod(key_path, 0o600)
    except Exception:
        pass
    return key


def encrypt_text(plaintext: str) -> str | None:
    """
    Encrypt a string with Fernet (AES-128-CBC + HMAC).
    Returns base64url-encoded ciphertext, or None if cryptography not installed.
    """
    Fernet = _get_fernet()
    if Fernet is None:
        return None
    try:
        f = Fernet(_get_or_create_key())
        return f.encrypt(plaintext.encode()).decode()
    except Exception as e:
        print(f'[security_utils] encrypt_text error: {e}')
        return None


def decrypt_text(token: str) -> str | None:
    """
    Decrypt a Fernet token.  Returns plaintext string or None on failure.
    """
    Fernet = _get_fernet()
    if Fernet is None:
        return None
    try:
        f = Fernet(_get_or_create_key())
        return f.decrypt(token.encode()).decode()
    except Exception as e:
        print(f'[security_utils] decrypt_text error: {e}')
        return None
