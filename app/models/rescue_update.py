
from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, Boolean

from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
from ..database import Base

import enum
from sqlalchemy import Enum

class RescueUpdateStatus(str, enum.Enum):
    acknowledged = "acknowledged"
    in_progress = "in_progress"
    resolved = "resolved"

class RescueUpdate(Base):
    __tablename__ = "rescue_updates"

    id = Column(Integer, primary_key=True)

    incident_id = Column(Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    rescue_team_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    
    status = Column(String, default=RescueUpdateStatus.acknowledged)
    is_acknowledged = Column(Boolean, default=False)
    acknowledged_at = Column(DateTime, nullable=True)
    in_progress_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    
    post_incident_report = Column(Text, nullable=True)
    post_incident_submitted_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
