
# pyrefly: ignore [missing-import]
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from datetime import datetime, timezone

# pyrefly: ignore [missing-import]
from sqlalchemy.orm import relationship
from ..database import Base

# pyrefly: ignore [missing-import]
from sqlalchemy.dialects.postgresql import UUID

class IncidentAssignment(Base):
    __tablename__ = "incident_assignments"

    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    team_name = Column(String, nullable=False)
    team_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    
    status = Column(String, default="Assigned") # Assigned, Accepted, Rejected, In Progress, Completed, Cancelled
    
    assigned_at = Column(DateTime, default=datetime.utcnow)
    accepted_at = Column(DateTime, nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    
    rejection_reason = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    incident = relationship("Incident", back_populates="assignments")
