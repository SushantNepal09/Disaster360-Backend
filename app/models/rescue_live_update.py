from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, Boolean, Float, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
import enum
from app.database import Base

class SeverityEnum(str, enum.Enum):
    NORMAL = "Normal"
    IMPORTANT = "Important"
    CRITICAL = "Critical"

class CategoryEnum(str, enum.Enum):
    ARRIVAL = "Arrival"
    RESCUE_ONGOING = "Rescue Ongoing"
    EVACUATION = "Evacuation"
    MEDICAL = "Medical"
    HAZARD = "Hazard"
    ROAD_BLOCKED = "Road Blocked"
    WEATHER = "Weather"
    RESOURCES_NEEDED = "Resources Needed"
    FIRE_UNDER_CONTROL = "Fire Under Control"
    GENERAL = "General"
    
class VisibilityEnum(str, enum.Enum):
    PUBLIC = "Public"
    ADMIN_ONLY = "Admin Only"

class RescueLiveUpdate(Base):
    __tablename__ = "rescue_live_updates"

    id = Column(Integer, primary_key=True, index=True)
    
    # Relationships
    incident_id = Column(Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    assignment_id = Column(Integer, ForeignKey("incident_assignments.id", ondelete="CASCADE"), nullable=False, index=True)
    team_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    team_name = Column(String, nullable=False)
    
    # Payload
    category = Column(String, nullable=False)
    severity = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    media_url = Column(String, nullable=True)
    
    # GPS (Optional)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    
    # Visibility and Audit
    visibility = Column(String, default=VisibilityEnum.PUBLIC.value)
    device_time = Column(DateTime, nullable=True)
    edited = Column(Boolean, default=False)
    deleted = Column(Boolean, default=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ORM Relationships
    incident = relationship("Incident")
    assignment = relationship("IncidentAssignment")
    team = relationship("User")
