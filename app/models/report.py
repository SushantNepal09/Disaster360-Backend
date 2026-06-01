
# pyrefly: ignore [missing-import]
from sqlalchemy import Column, Integer, String, Text, Float, ForeignKey, DateTime, Boolean

# pyrefly: ignore [missing-import]
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime, timezone

# pyrefly: ignore [missing-import]
from sqlalchemy.orm import relationship
from ..database import Base

class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    
    # User's specific description
    description = Column(Text)
    
    # Store images (you could also use report_media but storing one per report is simple)
    image = Column(String, nullable=True) 
    
    timestamp = Column(DateTime, default=datetime.utcnow)
    trust_score_snapshot = Column(Float, default=1.0)
    status = Column(String, default="Pending")
    verified = Column(Boolean, default=False)
    
    incident = relationship("Incident", back_populates="reports")
    user = relationship("User") 
    sms_details = relationship("SmsReport", back_populates="report", uselist=False, cascade="all, delete-orphan")
