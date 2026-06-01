
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta

from fastapi import HTTPException, status


# ==============================
# Configuration
# ==============================
SECRET_KEY = "Xz7!LmP9qR2vT6yU8iOp@aSdF1gH3jKl#ZxC4vBnM0qWeRtYuIoPaSdFgHjKlZxCv"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ==============================
# Password Hashing
# ==============================
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


# ==============================
# JWT Token Creation
# ==============================
def create_access_token(data: dict, expires_delta: timedelta = None): # type: ignore
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


# ==============================
# JWT Token Verification
# ==============================
def verify_access_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("user_id") # type: ignore
        role: str = payload.get("role") # type: ignore

        if user_id is None or role is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: user_id or role missing",
            )

        return payload

    except JWTError as e:
        print("JWTError:", str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {str(e)}",
        )
