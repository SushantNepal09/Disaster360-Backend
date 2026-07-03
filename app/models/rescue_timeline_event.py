from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, Boolean, JSON
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
from ..database import Base

class RescueTimelineEvent(Base):
    __tablename__ = "rescue_timeline_events"

    id = Column(Integer, primary_key=True, index=True)
    
    incident_id = Column(Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    assignment_id = Column(Integer, ForeignKey("incident_assignments.id", ondelete="CASCADE"), nullable=True)
    team_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    
    event_type = Column(String, nullable=False) # e.g., 'SYSTEM', 'MANUAL'
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    metadata_json = Column(JSON, nullable=True)
    
    is_system_generated = Column(Boolean, default=False)
