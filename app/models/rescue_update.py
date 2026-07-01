from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
from ..database import Base

class RescueUpdate(Base):
    __tablename__ = "rescue_updates"

    id = Column(Integer, primary_key=True)

    incident_id = Column(Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    rescue_team_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    
    # Append-only fields
    message = Column(Text, nullable=True)
    image_url = Column(String, nullable=True)
    location_lat = Column(String, nullable=True)
    location_lng = Column(String, nullable=True)
    
    # Reusing this for the final completion report
    post_incident_report = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
