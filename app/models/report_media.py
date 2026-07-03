
from datetime import datetime
from sqlalchemy.dialects.postgresql import UUID


from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint, DateTime
from ..database import Base

class ReportMedia(Base):
    __tablename__ = "report_media"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    incident_id = Column(Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=True) # Point media to incident
    report_id = Column(Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=True) # Point media to specific report
    file_path = Column(String)
    file_type = Column(String)
    assignment_id = Column(Integer, ForeignKey("incident_assignments.id", ondelete="CASCADE"), nullable=True) # Point media to specific operation update
    original_filename = Column(String, nullable=True)
    title = Column(String, nullable=True)
    file_size = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("incident_id", "file_path", name="uq_packet_file"),)
