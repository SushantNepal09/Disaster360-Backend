from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime
from datetime import datetime
from sqlalchemy.dialects.postgresql import UUID

# pyrefly: ignore [missing-import]
from ..database import Base

class StatusHistory(Base):
    __tablename__ = "status_history"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String, nullable=False, index=True) # 'Report', 'Incident', 'IncidentAssignment'
    entity_id = Column(Integer, nullable=False, index=True)
    
    old_status = Column(String, nullable=True)
    new_status = Column(String, nullable=False)
    
    changed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    remarks = Column(Text, nullable=True)
