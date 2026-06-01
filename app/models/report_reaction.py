
from sqlalchemy import Column, Integer, ForeignKey, Enum, UniqueConstraint

from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime

from sqlalchemy.orm import relationship
from ..database import Base
import enum

class ReactionType(str, enum.Enum):
    LIKE = "LIKE"
    DISLIKE = "DISLIKE"

class ReportReaction(Base):
    __tablename__ = "report_reactions"

    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    reaction_type = Column(Enum(ReactionType), nullable=False)

    __table_args__ = (
        UniqueConstraint('incident_id', 'user_id', name='uq_incident_user_reaction'),
    )

    incident = relationship("Incident", back_populates="reactions")
