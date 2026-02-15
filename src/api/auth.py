from datetime import datetime, timedelta, timezone
from hashlib import sha256

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.config import settings

security = HTTPBearer()

ALGORITHM = "HS256"


def _prepare_password(password: str) -> bytes:
    """Pre-hash with SHA-256 to handle passwords of any length (bcrypt truncates at 72 bytes)."""
    return sha256(password.encode()).hexdigest().encode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prepare_password(password), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(_prepare_password(plain_password), hashed_password.encode())


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.api_access_token_expire_minutes)
    )
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.api_secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> str:
    try:
        payload = jwt.decode(token, settings.api_secret_key, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
            )
        return username
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired"
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    return decode_access_token(credentials.credentials)
