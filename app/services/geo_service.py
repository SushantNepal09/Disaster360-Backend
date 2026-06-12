import math
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from ..models.user import User

def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance in kilometers between two points 
    on the earth (specified in decimal degrees) using Haversine formula.
    """
    R = 6371.0 # Radius of earth in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 + 
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * 
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def get_users_to_notify(db: Session, lat: float, lon: float, radius_km: float, incident_local_unit: str | None = None) -> List[User]:
    """
    Retrieve all users who match EITHER:
    1. Distance from (lat, lon) <= radius_km
    2. last_local_unit == incident_local_unit (case-insensitive)
    
    Uses Bounding Box SQL filtering to prevent loading all users into memory.
    """
    # Rough bounding box: 1 degree latitude is approx 111 km
    lat_diff = radius_km / 111.0
    lon_diff = radius_km / (111.0 * math.cos(math.radians(lat))) if lat != 0 else radius_km / 111.0
    
    query = db.query(User).filter(
        User.last_latitude.isnot(None),
        User.last_longitude.isnot(None)
    )
    
    filters = [
        and_(
            User.last_latitude.between(lat - lat_diff, lat + lat_diff),
            User.last_longitude.between(lon - lon_diff, lon + lon_diff)
        )
    ]
    
    if incident_local_unit:
        filters.append(User.last_local_unit.ilike(incident_local_unit))
        
    candidates = query.filter(or_(*filters)).all()
    
    final_users = []
    for user in candidates:
        # If matched by local unit, they are automatically included
        if incident_local_unit and user.last_local_unit and user.last_local_unit.lower() == incident_local_unit.lower():
            final_users.append(user)
            continue
            
        # Otherwise, verify exact distance since bounding box is a square, not a circle
        dist = calculate_distance(lat, lon, user.last_latitude, user.last_longitude) # type: ignore
        if dist <= radius_km:
            final_users.append(user)
            
    return final_users
