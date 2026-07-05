from sqlalchemy.orm import Session
from app.models.status_history import StatusHistory
from app.models.rescue_live_update import RescueLiveUpdate
from app.models.incident_assignment import IncidentAssignment
from app.models.user import User

class TimelineService:
    @staticmethod
    def build_incident_timeline(db: Session, incident_id: int):
        # 1. Get assignments to filter assignment history
        assignments = db.query(IncidentAssignment).filter(IncidentAssignment.incident_id == incident_id).all()
        assignment_ids = [a.id for a in assignments]

        # 2. Get Status History (Incident + Assignments)
        history_records = db.query(StatusHistory).filter(
            ((StatusHistory.entity_type == "Incident") & (StatusHistory.entity_id == incident_id)) |
            ((StatusHistory.entity_type == "Assignment") & (StatusHistory.entity_id.in_(assignment_ids)))
        ).all()

        # 3. Get Live Updates
        live_updates = db.query(RescueLiveUpdate).filter(
            RescueLiveUpdate.incident_id == incident_id
        ).all()

        timeline = []

        # Map Status History
        for h in history_records:
            role = "System"
            if h.changed_by:
                user = db.query(User).filter(User.id == h.changed_by).first()
                if user:
                    role = user.role.value if hasattr(user.role, 'value') else str(user.role)

            timeline.append({
                "type": "StatusHistory",
                "id": h.id,
                "entity_type": h.entity_type,
                "entity_id": h.entity_id,
                "old_status": h.old_status,
                "new_status": h.new_status,
                "changed_by": str(h.changed_by) if h.changed_by else None,
                "changed_by_role": role,
                "remarks": h.remarks,
                "created_at": h.timestamp.isoformat() if h.timestamp else None,
                "timestamp": h.timestamp
            })

        # Map Live Updates
        for u in live_updates:
            timeline.append({
                "type": "LiveUpdate",
                "id": u.id,
                "team_id": str(u.team_id),
                "team_name": u.team_name,
                "category": u.category,
                "severity": u.severity,
                "message": u.message,
                "media_url": u.media_url,
                "latitude": u.latitude,
                "longitude": u.longitude,
                "visibility": u.visibility,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "timestamp": u.created_at
            })

        # Sort combined timeline (Newest First = desc)
        # Sort key: timestamp (desc), then if same, LiveUpdate has priority, then by ID.
        def sort_key(item):
            # Prioritize StatusHistory slightly over LiveUpdate if they happen at the EXACT same second
            # actually prioritizing LiveUpdate to appear after assignment. Newest first means larger timestamp first.
            priority = 0 if item["type"] == "LiveUpdate" else 1
            return (item["timestamp"], priority, item["id"])
            
        timeline.sort(key=sort_key, reverse=True)
        
        # Remove raw timestamp used for sorting
        for item in timeline:
            del item["timestamp"]

        return timeline
