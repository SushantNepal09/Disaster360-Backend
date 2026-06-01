
from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from sqlalchemy.orm import relationship
from ..database import Base

class SmsReport(Base):
    __tablename__ = "sms_reports"

    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, unique=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    sender_number = Column(String, nullable=False, index=True)
    raw_message = Column(Text, nullable=False)
    metadata_json = Column(Text, nullable=True)  # Store any extra gateway info as JSON string
    received_at = Column(DateTime, default=datetime.utcnow)

    # Relationship back to the core report
    report = relationship("Report", back_populates="sms_details")
