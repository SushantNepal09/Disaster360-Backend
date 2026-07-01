# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session, joinedload
# pyrefly: ignore [missing-import]
from pydantic import BaseModel
from typing import List, Optional, Union
import math
from datetime import datetime
from app.database import get_db
from app.models.incident import Incident
from app.models.report import Report
from app.models.user import User
from app.routes.auth import get_current_user, get_optional_current_user
from app.models.report_embedding import ReportEmbedding
from app.models.rescue_update import RescueUpdate
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import object_session
from app.models.report_reaction import ReportReaction, ReactionType
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
        return [0.0] * 1536
    
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
        # pass
        return [0.0] * 1536

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
            "description": r.description,
            "severity": child_severity,
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
    if session:
        is_accepted = session.query(RescueUpdate).filter(RescueUpdate.incident_id == inc.id).first() is not None

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
            } for a in getattr(inc, 'assignments', []) if a.status != 'Cancelled'
        ],
        "rescue_team": ", ".join([a.team_name for a in getattr(inc, 'assignments', []) if a.status != 'Cancelled']) or "Not Assigned",
        "is_accepted": is_accepted,
        "media_urls": [m.file_path for m in inc.media if m.file_type == "image"] if hasattr(inc, "media") and inc.media else []
    }

def process_disaster_report(payload: ReportCreateRequest, db: Session, user_id: str | None = None):
    # Ensure location is a string for the database
    loc_val = payload.location
    if isinstance(loc_val, list):
        loc_val = ", ".join(str(x) for x in loc_val)

    DISASTER_RADIUS_KM = {"flood": 5.0, "landslide": 3.0, "earthquake": 50.0, "fire": 2.0, "default": 5.0}
    TEXT_THRESHOLD = 0.75

    RADIUS_KM = DISASTER_RADIUS_KM.get(payload.disaster_type.lower(), DISASTER_RADIUS_KM["default"])
    embedding = get_embedding(f"{payload.title}. {payload.description}")

    parsed_timestamp = datetime.utcnow()
    if payload.created_at:
        try:
            parsed_timestamp = datetime.fromisoformat(payload.created_at.replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            pass

    same_type_incidents = db.query(Incident).filter(Incident.disaster_type.ilike(payload.disaster_type)).all()
    nearby_incident_ids = []
    nearby_distances = {}

    for inc in same_type_incidents:
        if inc.latitude is None or inc.longitude is None: continue
        distance_km = calculate_distance(payload.latitude, payload.longitude, inc.latitude, inc.longitude)
        if distance_km <= RADIUS_KM:
            nearby_incident_ids.append(inc.id)
            nearby_distances[inc.id] = round(distance_km, 2)

    closest = None
    if nearby_incident_ids:
        closest = (
            db.query(ReportEmbedding, (1 - ReportEmbedding.embedding_vector.cosine_distance(embedding)).label("similarity"))
            .filter(ReportEmbedding.embedding_vector.isnot(None))
            .filter(ReportEmbedding.incident_id.in_(nearby_incident_ids))
            .order_by(ReportEmbedding.embedding_vector.cosine_distance(embedding))
            .first()
        )

    matched_incident = None
    similarity_score = 0.0

    if closest is not None:
        emb_row, sim = closest
        similarity_score = float(sim)
        if similarity_score >= TEXT_THRESHOLD:
            matched_incident = db.query(Incident).filter(Incident.id == emb_row.incident_id).first()

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
            timestamp=parsed_timestamp,
            status=matched_incident.status,
            verified=getattr(matched_incident, 'verified', False)
        )
        db.add(new_report)
        db.commit()
        db.refresh(new_report)

        distance_km = nearby_distances.get(matched_incident.id, 0.0)

        return new_report, {
            "message": "Your report matched an existing incident and has been merged.",
            "merged": True,
            "report_id": matched_incident.id,
            "submission_id": new_report.id,
            "disaster_type": payload.disaster_type,
            "similarity_score": round(similarity_score, 4),
            "distance_km": distance_km,
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
            timestamp=parsed_timestamp
        )
        db.add(new_report)
        db.commit()
        db.refresh(new_report)

        new_embedding = ReportEmbedding(incident_id=new_incident.id, embedding_vector=embedding)
        db.add(new_embedding)
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
