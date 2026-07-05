# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session, joinedload
# pyrefly: ignore [missing-import]
from pydantic import BaseModel
from typing import List, Optional, Union
import math
from datetime import datetime, timedelta
from app.database import get_db
from app.core.config import settings
from app.models.incident import Incident
from app.models.report import Report
from app.models.user import User
from app.models.report_media import ReportMedia
from app.routes.auth import get_current_user, get_optional_current_user
from app.models.report_embedding import ReportEmbedding
from app.models.rescue_timeline_event import RescueTimelineEvent
from app.models.rescue_update import RescueUpdate
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import object_session
from app.models.report_reaction import ReportReaction, ReactionType
from app.models.report_media import ReportMedia
from app.services.geo_service import get_users_to_notify
from app.services.notification_service import send_push_notification_task, NotificationType

# pyrefly: ignore [missing-import]
from google.genai import Client
# pyrefly: ignore [missing-import]
from google.genai import types
# pyrefly: ignore [missing-import]
from fastapi import APIRouter
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
import os

load_dotenv()
router = APIRouter(prefix="/reports", tags=["Reports/Incidents"])

def get_embedding(text: str) -> list[float]:
    # Check if API key is missing from environment instead of checking the client object
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key or api_key == "dummy_key_for_local_development":
        return [0.0] * settings.DUPLICATE_DETECTION_EMBEDDING_DIMENSIONS
    
    try:
        # Initialize client inside the function so it picks up .env changes
        client = Client(api_key=api_key)
        result = client.models.embed_content(
            model="gemini-embedding-001",
            contents=text,
            config=types.EmbedContentConfig(
                task_type="SEMANTIC_SIMILARITY",
                output_dimensionality=1536
            )
        )
        if not result.embeddings:
            raise ValueError("Google embedding API returned no embeddings")
        return result.embeddings[0].values
    except Exception as e:
        return [0.0] * settings.DUPLICATE_DETECTION_EMBEDDING_DIMENSIONS

class ReportCreateRequest(BaseModel):
    disaster_type: str
    title: str
    description: str
    location: Optional[Union[List[str], str]] = None
    latitude: float
    longitude: float
    severity: str
    created_at: Optional[str] = None

class ReportUpdateRequest(BaseModel):
    disaster_type: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    location: Optional[Union[List[str], str]] = None      
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    severity: Optional[str] = None

class DuplicateCheckRequest(BaseModel):
    title: str
    description: str

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def serialize_incident(inc, current_user_id=None, is_admin=False):
    submissions = []
    
    # Pre-fetch all media and reactions
    all_media = getattr(inc, "media", []) or []
    all_reactions = getattr(inc, "reactions", []) or []

    for r in inc.reports:
        user_name = r.user.full_name if (hasattr(r, "user") and r.user) else str(r.user_id)
        ts = r.timestamp.isoformat() if hasattr(r, 'timestamp') and r.timestamp else ''
        
        # Child specific fields
        child_title = r.title if getattr(r, 'title', None) else inc.title
        child_severity = r.severity if getattr(r, 'severity', None) else inc.severity
        
        # Child media: either strictly linked via report_id, or fallback to user_id if legacy (report_id is None)
        child_media = []
        for m in all_media:
            if m.file_type == "image":
                if m.report_id == r.id:
                    child_media.append(m.file_path)
                elif m.report_id is None and str(m.user_id) == str(r.user_id):
                    child_media.append(m.file_path)

        # Child reactions
        child_likes = sum(1 for rx in all_reactions if rx.reaction_type.value == "LIKE" and rx.report_id == r.id)
        child_dislikes = sum(1 for rx in all_reactions if rx.reaction_type.value == "DISLIKE" and rx.report_id == r.id)
        child_user_reaction = next((rx.reaction_type.value for rx in all_reactions if rx.report_id == r.id and str(rx.user_id) == str(current_user_id)), None)

        submissions.append({
            "id": r.id, 
            "user_id": str(r.user_id), 
            "user_name": user_name, 
            "title": child_title,
            "disaster_type": inc.disaster_type,
            "description": r.description,
            "severity": child_severity,
            "location": r.location if getattr(r, 'location', None) else inc.location,
            "latitude": r.latitude if getattr(r, 'latitude', None) else inc.latitude,
            "longitude": r.longitude if getattr(r, 'longitude', None) else inc.longitude,
            "timestamp": ts,
            "status": r.status,
            "verified": getattr(r, 'verified', False),
            "media_urls": list(set(child_media)),
            "likes": child_likes,
            "dislikes": child_dislikes,
            "user_reaction": child_user_reaction
        })

    likes = sum(1 for r in getattr(inc, 'reactions', []) if r.reaction_type.value == "LIKE")
    dislikes = sum(1 for r in getattr(inc, 'reactions', []) if r.reaction_type.value == "DISLIKE")
    user_reaction = None
    if current_user_id:
        for r in getattr(inc, 'reactions', []):
            if str(r.user_id) == str(current_user_id):
                user_reaction = r.reaction_type.value
                break
                
    session = object_session(inc)
    is_accepted = False
    rescue_updates = []
    if session:
        rescue_updates = session.query(RescueUpdate).filter(RescueUpdate.incident_id == inc.id).all()
        is_accepted = len(rescue_updates) > 0

    return {
        "id": inc.id,
        "user_id": submissions[0]["user_id"] if submissions else None,
        "disaster_type": inc.disaster_type,
        "title": inc.title,
        "description": inc.description,
        "location": inc.location,    
        "latitude": inc.latitude,
        "longitude": inc.longitude,
        "severity": inc.severity,
        "status": inc.status,
        "verified": getattr(inc, 'verified', False),
        "created_at": inc.created_at,
        "updated_at": inc.updated_at,
        "sources": inc.sources or len(submissions),
        "likes": likes,
        "dislikes": dislikes,
        "user_reaction": user_reaction,
        "submissions": submissions,
        "assigned_teams": [a.team_name for a in getattr(inc, 'assignments', []) if a.status != 'Cancelled'],
        "final_admin_report": getattr(inc, 'final_admin_report', None),
        "assignments": [
            {
                "id": str(a.id),
                "team_id": str(a.team_id),
                "team_name": a.team_name,
                "status": a.status if a.status else "Assigned",
                "accepted_at": getattr(a, 'accepted_at', None).isoformat() if getattr(a, 'accepted_at', None) else None,
                "rejection_reason": getattr(a, 'rejection_reason', None) if is_admin else None,
                "rejected_at": getattr(a, 'rejected_at', None).isoformat() if is_admin and getattr(a, 'rejected_at', None) else None,
                "completed_at": getattr(a, 'completed_at', None).isoformat() if getattr(a, 'completed_at', None) else None,
                "last_updated": getattr(a, 'updated_at', None).isoformat() if getattr(a, 'updated_at', None) else None,
                "post_incident_report": next((ru.post_incident_report for ru in rescue_updates if ru.rescue_team_id == a.team_id and ru.post_incident_report), None)
            } for a in getattr(inc, 'assignments', []) if a.status != 'Cancelled'
        ],
        "rescue_team": ", ".join([a.team_name for a in getattr(inc, 'assignments', []) if a.status != 'Cancelled']) or "Not Assigned",
        "is_accepted": is_accepted,
        "media_urls": [m.file_path for m in inc.media if m.file_type == "image"] if hasattr(inc, "media") and inc.media else []
    }

def process_disaster_report(payload: ReportCreateRequest, db: Session, user_id: str | None = None):
    # Ensure title and description contain actual text
    clean_title = (payload.title or "").strip()
    clean_desc = (payload.description or "").strip()
    
    if not clean_title and not clean_desc:
        raise HTTPException(status_code=400, detail="Cannot process report: Both title and description are empty.")
        
    embedding_text_parts = []
    if clean_title:
        embedding_text_parts.append(clean_title)
    if clean_desc:
        embedding_text_parts.append(clean_desc)
    
    embedding_text = ". ".join(embedding_text_parts)

    # Ensure location is a string for the database
    loc_val = payload.location
    if isinstance(loc_val, list):
        loc_val = ", ".join(str(x) for x in loc_val)

    RADIUS_KM = settings.get_radius(payload.disaster_type)
    TEXT_THRESHOLD = settings.DUPLICATE_DETECTION_SIMILARITY_THRESHOLD
    time_window = timedelta(hours=settings.DUPLICATE_DETECTION_TIME_WINDOW_HOURS)

    embedding = get_embedding(embedding_text)

    parsed_timestamp = datetime.utcnow()
    if payload.created_at:
        try:
            parsed_timestamp = datetime.fromisoformat(payload.created_at.replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            pass

    cutoff_time = datetime.utcnow() - time_window

    same_type_incidents = db.query(Incident).filter(
        Incident.disaster_type.ilike(payload.disaster_type),
        Incident.created_at >= cutoff_time,
        Incident.status.notin_(["Verified and Closed", "Resolved", "Rejected"])
    ).all()
    
    nearby_incident_ids = []

    for inc in same_type_incidents:
        is_nearby = False
        if inc.latitude is not None and inc.longitude is not None:
            if calculate_distance(payload.latitude, payload.longitude, inc.latitude, inc.longitude) <= RADIUS_KM:
                is_nearby = True
                
        if not is_nearby:
            for r in inc.reports:
                if r.latitude is not None and r.longitude is not None:
                    if calculate_distance(payload.latitude, payload.longitude, r.latitude, r.longitude) <= RADIUS_KM:
                        is_nearby = True
                        break
                        
        if is_nearby:
            nearby_incident_ids.append(inc.id)

    matched_incident = None
    similarity_score = 0.0

    if nearby_incident_ids:
        candidates = (
            db.query(ReportEmbedding, Incident, (1 - ReportEmbedding.embedding_vector.cosine_distance(embedding)).label("similarity"))
            .join(Incident, ReportEmbedding.incident_id == Incident.id)
            .filter(ReportEmbedding.embedding_vector.isnot(None))
            .filter(ReportEmbedding.incident_id.in_(nearby_incident_ids))
            .all()
        )
        
        valid_candidates = [c for c in candidates if float(c.similarity) >= TEXT_THRESHOLD]
        if valid_candidates:
            # Earliest incident selection
            valid_candidates.sort(key=lambda x: x[1].created_at)
            best_match = valid_candidates[0]
            matched_incident = best_match[1]
            similarity_score = float(best_match.similarity)

    if matched_incident:
        matched_incident.sources = (matched_incident.sources or 1) + 1 # type: ignore
        db.commit()
        db.refresh(matched_incident)
        
        new_report = Report(
            incident_id=matched_incident.id, 
            user_id=user_id, 
            title=payload.title,
            severity=payload.severity,
            description=payload.description,
            location=loc_val,
            latitude=payload.latitude,
            longitude=payload.longitude,
            timestamp=parsed_timestamp,
            status=matched_incident.status,
            verified=getattr(matched_incident, 'verified', False)
        )
        db.add(new_report)
        db.commit()
        db.refresh(new_report)
        
        event = RescueTimelineEvent(
            incident_id=matched_incident.id,
            created_by=user_id,
            event_type="SYSTEM",
            title=f"Citizen submitted {payload.disaster_type} report",
            is_system_generated=True,
            created_at=datetime.utcnow()
        )
        db.add(event)
        db.commit()

        return new_report, {
            "message": "Your report matched an existing incident and has been merged.",
            "merged": True,
            "report_id": matched_incident.id,
            "submission_id": new_report.id,
            "disaster_type": payload.disaster_type,
            "similarity_score": round(similarity_score, 4),
            "radius_used_km": RADIUS_KM,
            "sources": matched_incident.sources
        }
    else:
        new_incident = Incident(
            disaster_type=payload.disaster_type, title=payload.title, description=payload.description,
            location=loc_val, latitude=payload.latitude, longitude=payload.longitude,
            severity=payload.severity, sources=1, created_at=parsed_timestamp
        )
        db.add(new_incident)
        db.commit()
        db.refresh(new_incident)

        new_report = Report(
            incident_id=new_incident.id, 
            user_id=user_id, 
            title=payload.title,
            severity=payload.severity,
            description=payload.description, 
            location=loc_val,
            latitude=payload.latitude,
            longitude=payload.longitude,
            timestamp=parsed_timestamp
        )
        db.add(new_report)
        db.commit()
        db.refresh(new_report)

        new_embedding = ReportEmbedding(incident_id=new_incident.id, embedding_vector=embedding)
        db.add(new_embedding)
        db.commit()

        event = RescueTimelineEvent(
            incident_id=new_incident.id,
            created_by=user_id,
            event_type="SYSTEM",
            title=f"Citizen submitted {payload.disaster_type} report",
            is_system_generated=True,
            created_at=datetime.utcnow()
        )
        db.add(event)
        db.commit()

        return new_report, {
            "message": "New disaster report created successfully.",
            "merged": False,
            "report_id": new_incident.id,
            "submission_id": new_report.id,
            "disaster_type": payload.disaster_type,
            "radius_used_km": RADIUS_KM,
            "sources": 1
        }

@router.post("/")
def create_report(
    payload: ReportCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    _, response_data = process_disaster_report(payload, db, current_user.id)
    
    incident_id = response_data.get("report_id")
    
    # Step 7: Earthquake Broadcast (NATIONAL)
    if payload.disaster_type.lower() == "earthquake":
        # Get all users with FCM tokens (except current user)
        all_users = db.query(User).filter(User.fcm_token.isnot(None), User.id != current_user.id).all()
        user_ids = [str(u.id) for u in all_users]
        if user_ids:
            background_tasks.add_task(
                send_push_notification_task,
                user_ids,
                NotificationType.EARTHQUAKE_ALERT,
                "EARTHQUAKE ALERT",
                "Take immediate safety precautions!",
                {"incident_id": str(incident_id), "type": "earthquake"}
            )
            
    # Step 4: New Incident Created (after deduplication fails)
    elif not response_data.get("merged"):
        radius_km = response_data.get("radius_used_km")
        
        incident_local_unit = None
        if payload.location:
            loc_val = payload.location
            if isinstance(loc_val, list):
                loc_val = ", ".join(str(x) for x in loc_val)
            parts = loc_val.split(",")
            incident_local_unit = parts[-1].strip() if parts else loc_val.strip()
            
        nearby_users = get_users_to_notify(db, payload.latitude, payload.longitude, radius_km, incident_local_unit)
        user_ids = [str(u.id) for u in nearby_users if str(u.id) != str(current_user.id)]
        
        if user_ids:
            background_tasks.add_task(
                send_push_notification_task,
                user_ids,
                NotificationType.INCIDENT_CREATED,
                f"New {payload.disaster_type.capitalize()} Reported",
                f"A new incident has been reported near your location.",
                {"incident_id": str(incident_id)}
            )
            
    return response_data

@router.get("/", response_model=List[dict])
def get_reports(db: Session = Depends(get_db), current_user: User | None = Depends(get_optional_current_user)):
    query = db.query(Incident)
    
    # If not logged in, or not an admin, hide rejected reports
    if not current_user or current_user.role != "admin":
        query = query.filter(Incident.status != "Rejected")
        
    incidents = (
        query
        .options(joinedload(Incident.reports).joinedload(Report.user), joinedload(Incident.reactions))
        .all()
    )
    user_id = current_user.id if current_user else None
    is_admin_flag = current_user is not None and current_user.role == "admin"
    return [serialize_incident(inc, user_id, is_admin=is_admin_flag) for inc in incidents if inc.reports]

@router.get("/verified", response_model=List[dict])
def get_verified_reports(db: Session = Depends(get_db), current_user: User | None = Depends(get_optional_current_user)):
    incidents = db.query(Incident).filter(Incident.status == "Verified").options(joinedload(Incident.reports).joinedload(Report.user), joinedload(Incident.reactions)).all()
    if not incidents: raise HTTPException(status_code=404, detail="No verified reports found")
    user_id = current_user.id if current_user else None
    return [serialize_incident(inc, user_id) for inc in incidents]

@router.get("/my-reports", response_model=List[dict])
def get_my_reports(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reports = db.query(Report).filter(Report.user_id == current_user.id).options(joinedload(Report.incident)).all()
    if not reports: raise HTTPException(status_code=404, detail="You have not posted any reports")
    inc_ids = list(set([r.incident_id for r in reports]))
    incidents = db.query(Incident).filter(Incident.id.in_(inc_ids)).options(joinedload(Incident.reports).joinedload(Report.user), joinedload(Incident.reactions)).all()
    return [serialize_incident(inc, current_user.id) for inc in incidents]

@router.get("/nearby", response_model=List[dict])
def get_nearby_reports(lat: float, lon: float, radius: float = 5.0, db: Session = Depends(get_db), current_user: User | None = Depends(get_optional_current_user)):
    incidents = db.query(Incident).filter(Incident.status != "Rejected").options(joinedload(Incident.reports).joinedload(Report.user), joinedload(Incident.reactions)).all()
    if not incidents: raise HTTPException(status_code=404, detail="No incidents found")

    nearby = []
    user_id = current_user.id if current_user else None
    for inc in incidents:
        distance = calculate_distance(lat, lon, inc.latitude, inc.longitude)
        if distance <= radius:
            data = serialize_incident(inc, user_id)
            data["distance_km"] = round(distance, 2)
            nearby.append(data)
    if not nearby: raise HTTPException(status_code=404, detail=f"No incidents found within {radius} km")
    nearby.sort(key=lambda x: x["distance_km"])
    return nearby

@router.delete("/{report_id}")
def delete_own_report(report_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Note: report_id from the frontend is actually the Incident ID
    reports = db.query(Report).filter(Report.incident_id == report_id, Report.user_id == current_user.id).all()
    if not reports: raise HTTPException(status_code=404, detail="Report not found or not yours")
    
    for report in reports:
        db.delete(report)
    db.commit()
    
    inc = db.query(Incident).filter(Incident.id == report_id).first()
    if inc and len(inc.reports) == 0:
        db.delete(inc)
        db.commit()
    elif inc:
        inc.sources -= len(reports)
        db.commit()
        
    return {"message": "Report deleted successfully"}

@router.get("/{report_id}")
def get_report(report_id: int, db: Session = Depends(get_db), current_user: User | None = Depends(get_optional_current_user)):
    inc = db.query(Incident).filter(Incident.id == report_id).options(joinedload(Incident.reports).joinedload(Report.user), joinedload(Incident.reactions)).first()
    if not inc: raise HTTPException(status_code=404, detail="Incident not found")
    user_id = current_user.id if current_user else None
    is_admin_flag = current_user is not None and current_user.role == "admin"
    return serialize_incident(inc, user_id, is_admin=is_admin_flag)

@router.put("/{report_id}")
def update_report(
    report_id: int,
    payload: ReportUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    inc = db.query(Incident).filter(Incident.id == report_id).first()
    if not inc: raise HTTPException(status_code=404, detail="Incident not found")
    
    # Check if the user is authorized (must be a reporter on this incident or an admin)
    user_report = db.query(Report).filter(Report.incident_id == report_id, Report.user_id == current_user.id).first()
    if not user_report and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to update this incident")
    
    if payload.disaster_type: inc.disaster_type = payload.disaster_type
    if payload.title: inc.title = payload.title
    if payload.description: 
        inc.description = payload.description
        if user_report:
            user_report.description = payload.description
    if payload.location: inc.location = payload.location
    if payload.latitude: inc.latitude = payload.latitude
    if payload.longitude: inc.longitude = payload.longitude
    if payload.severity: inc.severity = payload.severity
    db.commit()
    db.refresh(inc)
    return {"message": "Incident and report updated", "report_id": inc.id}

@router.post("/{report_id}/react")
def react_to_report(
    report_id: int, 
    reaction: str, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    if reaction not in ["LIKE", "DISLIKE"]:
        raise HTTPException(status_code=400, detail="Invalid reaction type")

    inc = db.query(Incident).filter(Incident.id == report_id).first()
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    existing_reaction = db.query(ReportReaction).filter(
        ReportReaction.incident_id == report_id,
        ReportReaction.report_id.is_(None),
        ReportReaction.user_id == current_user.id
    ).first()

    if existing_reaction:
        if existing_reaction.reaction_type.value == reaction:
            db.delete(existing_reaction)
            db.commit()
        else:
            existing_reaction.reaction_type = ReactionType(reaction)
            db.commit()
    else:
        new_reaction = ReportReaction(
            incident_id=report_id,
            report_id=None,
            user_id=current_user.id,
            reaction_type=ReactionType(reaction)
        )
        db.add(new_reaction)
        db.commit()

    all_reactions = db.query(ReportReaction).filter(
        ReportReaction.incident_id == report_id,
        ReportReaction.report_id.is_(None)
    ).all()
    likes = sum(1 for r in all_reactions if r.reaction_type.value == "LIKE")
    dislikes = sum(1 for r in all_reactions if r.reaction_type.value == "DISLIKE")
    
    user_new_reaction = None
    for r in all_reactions:
        if str(r.user_id) == str(current_user.id):
            user_new_reaction = r.reaction_type.value
            break

    return {"likes": likes, "dislikes": dislikes, "user_reaction": user_new_reaction}

@router.post("/submissions/{sub_id}/react")
def react_to_submission(
    sub_id: int, 
    reaction: str, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    if reaction not in ["LIKE", "DISLIKE"]:
        raise HTTPException(status_code=400, detail="Invalid reaction type")

    sub = db.query(Report).filter(Report.id == sub_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")

    existing_reaction = db.query(ReportReaction).filter(
        ReportReaction.incident_id == sub.incident_id,
        ReportReaction.report_id == sub_id,
        ReportReaction.user_id == current_user.id
    ).first()

    if existing_reaction:
        if existing_reaction.reaction_type.value == reaction:
            db.delete(existing_reaction)
            db.commit()
        else:
            existing_reaction.reaction_type = ReactionType(reaction)
            db.commit()
    else:
        new_reaction = ReportReaction(
            incident_id=sub.incident_id,
            report_id=sub_id,
            user_id=current_user.id,
            reaction_type=ReactionType(reaction)
        )
        db.add(new_reaction)
        db.commit()

    all_reactions = db.query(ReportReaction).filter(
        ReportReaction.incident_id == sub.incident_id,
        ReportReaction.report_id == sub_id
    ).all()
    likes = sum(1 for r in all_reactions if r.reaction_type.value == "LIKE")
    dislikes = sum(1 for r in all_reactions if r.reaction_type.value == "DISLIKE")
    
    user_new_reaction = None
    for r in all_reactions:
        if str(r.user_id) == str(current_user.id):
            user_new_reaction = r.reaction_type.value
            break

    return {"likes": likes, "dislikes": dislikes, "user_reaction": user_new_reaction}


# ======================
# Get Unified Incident Timeline
# ======================
@router.get("/incidents/{incident_id}/timeline")
def get_incident_timeline(
    incident_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_optional_current_user)
):
    """
    Returns the chronologically ordered timeline for a specific incident,
    merging StatusHistory and RescueLiveUpdates.
    """
    from app.services.timeline_service import TimelineService
    from app.models.incident import Incident
    
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
        
    timeline_data = TimelineService.build_incident_timeline(db, incident_id)
    
    # Role-based filtering / serialization
    is_admin = False
    if current_user and str(getattr(current_user, 'role', '')).lower() == 'admin':
        is_admin = True
        
    filtered_timeline = []
    for item in timeline_data:
        if item["type"] == "LiveUpdate":
            # If ADMIN_ONLY, exclude from citizens
            if item.get("visibility") == "Admin Only" and not is_admin:
                continue
                
        filtered_timeline.append(item)
        
    return {
        "success": True,
        "data": filtered_timeline
    }

# ======================
# Get Rescue Timeline (RescueTimelineEvent)
# ======================
@router.get("/{incident_id}/rescue-timeline")
def get_rescue_timeline(
    incident_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user)
):
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    events = db.query(
        RescueTimelineEvent,
        User.full_name.label("team_name"),
        ReportMedia.file_path.label("media_url")
    ).outerjoin(
        User, RescueTimelineEvent.team_id == User.id
    ).outerjoin(
        ReportMedia, RescueTimelineEvent.media_id == ReportMedia.id
    ).filter(
        RescueTimelineEvent.incident_id == incident_id
    ).order_by(RescueTimelineEvent.created_at.asc()).all()

    data = []
    for event, team_name, media_url in events:
        data.append({
            "id": event.id,
            "title": event.title,
            "description": event.description,
            "event_type": event.event_type,
            "created_at": event.created_at.isoformat() if event.created_at else None,
            "team_name": team_name,
            "media_url": media_url,
            "is_system_generated": event.is_system_generated
        })

    return {
        "success": True,
        "data": data
    }


# ======================
# Get Admin Post Disaster Report
# ======================
@router.get("/{incident_id}/admin-report")
def get_admin_post_disaster_report(
    incident_id: int,
    db: Session = Depends(get_db)
):
    inc = db.query(Incident).filter(Incident.id == incident_id).first()
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    from app.models.admin_report_attachment import AdminReportAttachment
    attachments = db.query(AdminReportAttachment).filter(AdminReportAttachment.incident_id == incident_id).all()

    att_list = []
    for att in attachments:
        att_list.append({
            "id": att.id,
            "original_filename": att.original_filename,
            "file_url": att.file_url,
            "file_size": att.file_size,
            "uploaded_at": att.uploaded_at.isoformat() if att.uploaded_at else None
        })

    return {
        "success": True,
        "data": {
            "incident_id": incident_id,
            "description": inc.final_admin_report,
            "closed_date": inc.updated_at.isoformat() if inc.updated_at else None,
            "attachments": att_list
        }
    }
