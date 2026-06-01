from sqlalchemy import Column, Integer, String
from ..database import Base

class RiskZone(Base):
    __tablename__ = "risk_zones"

    id = Column(Integer, primary_key=True)

    ward_name = Column(String)
    district = Column(String)

    risk_level = Column(String)
    disaster_type = Column(String)
