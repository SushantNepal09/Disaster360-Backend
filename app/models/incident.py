
# pyrefly: ignore [missing-import]
# pyrefly: ignore [missing-import]
from sqlalchemy import Column, Integer, String, Text, Float, DateTime, Boolean
from datetime import datetime, timezone
from app.core.statuses import IncidentStatus

# pyrefly: ignore [missing-import]
from sqlalchemy.orm import relationship
from app.database import Base

class Incident(Base):
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True, index=True)
    disaster_type = Column(String)
    title = Column(String)
    description = Column(Text)
    location = Column(String, nullable=True)  
    latitude = Column(Float)
    longitude = Column(Float)
    severity = Column(String)
    status = Column(String, default=IncidentStatus.PENDING)
    verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.now(timezone.utc))
    final_admin_report = Column(Text, nullable=True)

    # Calculate trust aggregation based on report count or trust score
    sources = Column(Integer, default=1)

    reports = relationship("Report", back_populates="incident", cascade="all, delete-orphan")
    reactions = relationship("ReportReaction", back_populates="incident", cascade="all, delete-orphan")
    assignments = relationship("IncidentAssignment", back_populates="incident", cascade="all, delete-orphan")
    media = relationship("ReportMedia", backref="incident", cascade="all, delete-orphan")
