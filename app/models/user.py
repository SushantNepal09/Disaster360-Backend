
# pyrefly: ignore [missing-import]
from sqlalchemy import Column, String, Boolean, Date, DateTime, Float

# pyrefly: ignore [missing-import]
from sqlalchemy.dialects.postgresql import UUID
import uuid

# pyrefly: ignore [missing-import]
from sqlalchemy.sql import func
from ..database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name = Column(String)
    email = Column(String, unique=True, index=True, nullable=False)
    phone = Column(String)
    password_hash = Column(String, nullable=False)
    citizenship_number = Column(String, unique=True)
    citizenship_issue_date = Column(String)
    citizenship_issue_district = Column(String)
    specialization = Column(String, nullable=True) # E.g., Firefighter, Medical, Police, SAR
    role = Column(String, default="citizen")

    is_admin = Column(Boolean, nullable=False, default=False, server_default="false")
    is_rescueteam = Column(Boolean, nullable=False, default=False, server_default="false")

    # Email Verification Fields
    is_verified = Column(Boolean, nullable=False, default=False, server_default="false")
    verification_token = Column(String, nullable=True)
    verification_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Push Notification & Geo-Location Fields
    fcm_token = Column(String, nullable=True)
    last_latitude = Column(Float, nullable=True)
    last_longitude = Column(Float, nullable=True)
    last_local_unit = Column(String, nullable=True)
    last_location_update = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
