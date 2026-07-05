from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, BigInteger
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.database import Base

class PostIncidentReport(Base):
    __tablename__ = "post_incident_reports"

    id = Column(Integer, primary_key=True, index=True)
    assignment_id = Column(Integer, ForeignKey("incident_assignments.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    incident_id = Column(Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.now(timezone.utc))

    assignment = relationship("IncidentAssignment", backref="post_incident_report")
    attachments = relationship("PostIncidentReportAttachment", back_populates="report", cascade="all, delete-orphan")


class PostIncidentReportAttachment(Base):
    __tablename__ = "post_incident_report_attachments"

    id = Column(Integer, primary_key=True, index=True)
    post_incident_report_id = Column(Integer, ForeignKey("post_incident_reports.id", ondelete="CASCADE"), nullable=False, index=True)
    
    original_filename = Column(String(255), nullable=False)
    stored_filename = Column(String(255), unique=True, nullable=False)
    file_url = Column(String(1024), nullable=False)
    file_size = Column(BigInteger, nullable=False)
    mime_type = Column(String(100), nullable=False)
    
    uploaded_at = Column(DateTime, default=datetime.utcnow, index=True)

    report = relationship("PostIncidentReport", back_populates="attachments")
