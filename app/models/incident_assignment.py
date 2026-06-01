
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from datetime import datetime, timezone

from sqlalchemy.orm import relationship
from ..database import Base

class IncidentAssignment(Base):
    __tablename__ = "incident_assignments"

    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    team_name = Column(String, nullable=False)
    assigned_at = Column(DateTime, default=datetime.utcnow)

    incident = relationship("Incident", back_populates="assignments")
