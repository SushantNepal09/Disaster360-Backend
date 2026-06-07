
# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, Request

# pyrefly: ignore [missing-import]
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm

# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session

# pyrefly: ignore [missing-import]
from pydantic import BaseModel, EmailStr
import uuid
import secrets
from datetime import datetime, timedelta, timezone

# pyrefly: ignore [missing-import]
from pydantic import BaseModel, EmailStr

from ..database import get_db
from ..models.user import User
from ..auth.auth_utils import (
    hash_password,
    verify_password,
    create_access_token,
    verify_access_token,
)
from ..auth.email_service import send_verification_email

router = APIRouter(prefix="/auth", tags=["Authentication"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# ==============================
# Helper: safely cast is_admin to bool
# ==============================
def is_admin_approved(user: User) -> bool:
    return str(user.is_admin).lower() == "true"

def is_rescue_approved(user: User) -> bool:
    return str(user.is_rescueteam).lower() == "true"



# ======================
# Pydantic Schema
# ======================
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    role: str = "citizen"
    full_name: str | None = None
    phone: str | None = None
    citizenship_number: str | None = None
    citizenship_issue_district: str | None = None
    citizenship_issue_date: str | None = None
    specialization: str | None = None

class ResendVerificationRequest(BaseModel):
    email: EmailStr


# ======================
# Register
# ======================
@router.post("/register")
def register(request: Request, payload: RegisterRequest, db: Session = Depends(get_db)):

    existing_user = db.query(User).filter(User.email == payload.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    if payload.citizenship_number:
        existing_citizenship = db.query(User).filter(User.citizenship_number == payload.citizenship_number).first()
        if existing_citizenship:
            raise HTTPException(status_code=400, detail="Citizenship number already registered")

        if payload.citizenship_issue_date:
            parts = payload.citizenship_number.split("-")
            if len(parts) == 4:
                third_section = parts[2]
                issue_year = payload.citizenship_issue_date[:4]
                if len(issue_year) == 4:
                    last_two_digits = issue_year[-2:]
                    if third_section != last_two_digits:
                        raise HTTPException(status_code=400, detail="Citizenship number invalid")

    if payload.role.lower() not in ["citizen", "admin", "rescue"]:
        raise HTTPException(status_code=400, detail="Role must be 'citizen', 'admin', or 'rescue'")

    new_user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=payload.role.lower(),
        full_name=payload.full_name,
        phone=payload.phone,
        citizenship_number=payload.citizenship_number,
        citizenship_issue_district=payload.citizenship_issue_district,
        citizenship_issue_date=payload.citizenship_issue_date,  # Note: backend stores as Date but SQLAlchemy handles ISO 8601 string cast natively for Date columns in Postgres/SQLite
        specialization=payload.specialization if payload.role.lower() == "rescue" else None,
        is_admin=False,  
        is_rescueteam=False,
        is_verified=False,
        verification_token=secrets.token_urlsafe(32),
        verification_expires_at=datetime.now(timezone.utc) + timedelta(minutes=30)
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    if payload.role.lower() == "admin":
        message = "Admin registered successfully. Please check your email to verify your account. You cannot login until manually approved in the database."
    elif payload.role.lower() == "rescue":
        message = "Rescue Team registered successfully. Please check your email to verify your account. You cannot login until manually approved by Admin."
    else:
        message = "Citizen registered successfully. Please check your email to verify your account."

    # Send verification email asynchronously (in a real app, use background tasks)
    backend_url = str(request.base_url).rstrip("/")
    send_verification_email(new_user.email, new_user.verification_token, backend_url=backend_url)

    return {"message": message}


# ======================
# Verify Email
# ======================
@router.get("/verify-email")
def verify_email(token: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.verification_token == token).first()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid verification token.")

    # Convert to timezone aware if needed, SQLAlchemy might return naive depending on DB driver
    expires_at = user.verification_expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if not expires_at or expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Verification token has expired.")

    user.is_verified = True
    user.verification_token = None
    user.verification_expires_at = None
    db.commit()

    # For API response (You could also return an HTML response here if users click it from a browser)
    return {"message": "Email verified successfully. You can now log in."}


# ======================
# Resend Verification
# ======================
@router.post("/resend-verification")
def resend_verification(request: Request, payload: ResendVerificationRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()

    if not user:
        # Don't reveal if email exists or not
        return {"message": "If the email is registered and unverified, a verification link has been sent."}

    if user.is_verified:
        return {"message": "Email is already verified."}

    user.verification_token = secrets.token_urlsafe(32)
    user.verification_expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    db.commit()

    backend_url = str(request.base_url).rstrip("/")
    send_verification_email(user.email, user.verification_token, backend_url=backend_url)

    return {"message": "If the email is registered and unverified, a verification link has been sent."}


# ======================
# Login
# ======================
@router.post("/login")
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    # OAuth2PasswordRequestForm sends email in the "username" field in Swagger
    user = db.query(User).filter(User.email == form_data.username).first()

    if not user or not verify_password(form_data.password, user.password_hash): # type: ignore
        raise HTTPException(status_code=400, detail="Invalid credentials")

    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Please verify your email first")

    # ✅ Citizen → always allowed
    # ✅ Admin not approved → strictly blocked
    # ✅ Admin approved → allowed
    if user.role == "admin" and not is_admin_approved(user): # type: ignore
        raise HTTPException(
            status_code=403,
            detail="Admin account pending approval. Contact system administrator."
        )
    if user.role == "rescue" and not is_rescue_approved(user): # type: ignore
        raise HTTPException(
            status_code=403,
            detail="Rescue Team account pending approval. Contact system administrator."
        )

    access_token = create_access_token(
        data={
            "user_id": str(user.id),
            "role": user.role
        }
    )

    return {"access_token": access_token, "token_type": "bearer"}


# ======================
# Profile
# ======================
@router.get("/profile")
def get_profile(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):
    payload = verify_access_token(token)
    user = db.query(User).filter(User.id == uuid.UUID(payload.get("user_id"))).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "is_admin": user.is_admin,
        "is_rescueteam": user.is_rescueteam,
        "full_name": user.full_name,
        "phone": user.phone,
        "citizenship_number": user.citizenship_number,
        "citizenship_issue_date": str(user.citizenship_issue_date) if user.citizenship_issue_date else None,
        "citizenship_issue_district": user.citizenship_issue_district,
        "specialization": user.specialization
    }

class ProfileUpdateRequest(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    specialization: str | None = None

@router.post("/profile")
def update_profile(
    payload: ProfileUpdateRequest,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):
    token_payload = verify_access_token(token)
    user = db.query(User).filter(User.id == uuid.UUID(token_payload.get("user_id"))).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.phone is not None:
        user.phone = payload.phone
    if payload.specialization is not None:
        user.specialization = payload.specialization
    
    db.commit()
    db.refresh(user)
    
    return {"message": "Profile updated successfully"}


# ======================
# Dependency: any logged-in citizen OR approved admin
# ======================
def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    payload = verify_access_token(token)
    user = db.query(User).filter(User.id == uuid.UUID(payload.get("user_id"))).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # ✅ Double safety — blocks admin if revoked in DB after token was issued
    if user.role == "admin" and not is_admin_approved(user): # type: ignore
        raise HTTPException(
            status_code=403,
            detail="Admin access has been revoked. Contact system administrator."
        )

    return user


# pyrefly: ignore [missing-import]
from fastapi import Request

def get_optional_current_user(
    request: Request,
    db: Session = Depends(get_db)
) -> User | None:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ")[1]
    try:
        payload = verify_access_token(token)
        user = db.query(User).filter(User.id == uuid.UUID(payload.get("user_id"))).first()
        return user
    except Exception:
        return None


# ======================
# Dependency: approved admin only
# ======================
def get_current_admin(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    payload = verify_access_token(token)

    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    user = db.query(User).filter(User.id == uuid.UUID(payload.get("user_id"))).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not is_admin_approved(user):
        raise HTTPException(
            status_code=403,
            detail="Admin access has been revoked. Contact system administrator."
        )

    return user
# ======================
# Dependency: approved rescue team only
# ======================
def get_current_rescue_team(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    payload = verify_access_token(token)

    if payload.get("role") != "rescue":
        raise HTTPException(status_code=403, detail="Rescue Team access required")

    user = db.query(User).filter(User.id == uuid.UUID(payload.get("user_id"))).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not is_rescue_approved(user):
        raise HTTPException(
            status_code=403,
            detail="Rescue Team access has been revoked. Contact system administrator."
        )

    return user
