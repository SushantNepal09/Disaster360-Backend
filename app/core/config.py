import os
from typing import Dict

class Settings:
    # Duplicate Detection Settings
    DUPLICATE_DETECTION_TIME_WINDOW_HOURS: int = int(os.getenv("DUPLICATE_TIME_WINDOW_HOURS", "2"))
    DUPLICATE_DETECTION_SIMILARITY_THRESHOLD: float = float(os.getenv("DUPLICATE_SIMILARITY_THRESHOLD", "0.75"))
    DUPLICATE_DETECTION_EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
    DUPLICATE_DETECTION_EMBEDDING_DIMENSIONS: int = 1536
    
    # Disaster-specific radii in kilometers
    # Format: {"disaster_type": radius_in_km}
    DISASTER_RADIUS_KM: Dict[str, float] = {
        "flood": 5.0,
        "landslide": 3.0,
        "earthquake": 50.0,
        "fire": 2.0,
        "default": 5.0
    }
    
    def get_radius(self, disaster_type: str) -> float:
        return self.DISASTER_RADIUS_KM.get(disaster_type.lower(), self.DISASTER_RADIUS_KM["default"])

settings = Settings()
