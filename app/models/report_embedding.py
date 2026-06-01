from sqlalchemy import Column, Integer, ForeignKey
from pgvector.sqlalchemy import Vector
from ..database import Base

class ReportEmbedding(Base):
    __tablename__ = "report_embeddings"

    id = Column(Integer, primary_key=True)
    incident_id = Column(Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=True) # Changed from report_id to incident_id
    embedding_vector = Column(Vector(1536))
