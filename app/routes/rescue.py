
# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException

# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session

# pyrefly: ignore [missing-import]
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from ..database import get_db
from ..models.incident import Incident
from ..models.rescue_update import RescueUpdate
from ..models.user import User
from .auth import get_current_rescue_team

router = APIRouter(prefix="/rescue", tags=["Rescue Team"])


# ======================
# Pydantic Schemas
# ======================
class AcknowledgeRequest(BaseModel):
    incident_id: int


class StatusUpdateRequest(BaseModel):
    status: str  # Acknowledged | Rescue In Progress | Controlled | Closed


class PostIncidentReportRequest(BaseModel):
    post_incident_report: str


# ======================
# Get Rescue Team Profile + Stats
# ======================
@router.get("/profile")
def get_rescue_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    """Returns the rescue team member's profile data + mission statistics."""
    all_ops = db.query(RescueUpdate).filter(
        RescueUpdate.rescue_team_id == current_user.id
    ).all()

    total = len(all_ops)
    active = len([op for op in all_ops if op.status in ["Acknowledged", "Rescue In Progress"]])
    completed = len([op for op in all_ops if op.status in ["Controlled", "Closed"]])

    return {
        "id": str(current_user.id),
        "full_name": current_user.full_name,
        "email": current_user.email,
        "phone": getattr(current_user, "phone", None),
        "role": current_user.role,
        "specialization": getattr(current_user, "specialization", None),
        "stats": {
            "total_operations": total,
            "active_operations": active,
            "completed_operations": completed,
        }
    }


# ======================
# View All Verified Reports (rescue team panel)
# After admin verifies a report it appears here instantly
# Returns empty list (not 404) when no reports available
# ======================
@router.get("/verified-reports", response_model=List[dict])
def get_verified_reports(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    reports = db.query(Incident).filter(Incident.status == "Verified").all()

    result = []
    for r in reports:
        # Check if this rescue team member has already acknowledged this incident
        rescue_update = db.query(RescueUpdate).filter(
            RescueUpdate.incident_id == r.id,
            RescueUpdate.rescue_team_id == current_user.id
        ).first()

        result.append({
            "id": r.id,
            "disaster_type": r.disaster_type,
            "title": r.title,
            "description": r.description,
            "location": r.location,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "severity": r.severity,
            "status": r.status,
            "rescue_status": rescue_update.status if rescue_update else "Not Acknowledged",
            "rescue_update_id": rescue_update.id if rescue_update else None,
            "is_acknowledged": rescue_update.is_acknowledged if rescue_update else False,
            "post_incident_report": rescue_update.post_incident_report if rescue_update else None,
            "assigned_teams": [a.team_name for a in r.assignments],
            "media_urls": [m.file_path for m in r.media] if r.media else [],
            "created_at": r.created_at,
            "updated_at": r.updated_at
        })

    return result


# ======================
# Acknowledge / Accept a Rescue Assignment
# ======================
@router.post("/acknowledge")
def acknowledge_report(
    payload: AcknowledgeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    # Check report exists and is verified
    report = db.query(Incident).filter(Incident.id == payload.incident_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Incident not found")

    if report.status != "Verified": # type: ignore
        raise HTTPException(
            status_code=400,
            detail="Only verified incidents can be acknowledged"
        )

    # Check if already acknowledged by this rescue team member
    existing = db.query(RescueUpdate).filter(
        RescueUpdate.incident_id == payload.incident_id,
        RescueUpdate.rescue_team_id == current_user.id
    ).first()

    if existing:
        raise HTTPException(
            status_code=400,
            detail="You have already acknowledged this incident"
        )

    # Create rescue update entry
    rescue_update = RescueUpdate(
        incident_id=payload.incident_id,
        rescue_team_id=current_user.id,
        status="Acknowledged",
        is_acknowledged=True,
        acknowledged_at=datetime.utcnow()
    )

    db.add(rescue_update)
    db.commit()
    db.refresh(rescue_update)

    return {
        "message": f"Incident {payload.incident_id} acknowledged successfully",
        "rescue_update_id": rescue_update.id,
        "acknowledged_by": current_user.email,
        "acknowledged_at": rescue_update.acknowledged_at
    }


# ======================
# Update Rescue Operation Status
# Lifecycle: Acknowledged → Rescue In Progress → Controlled → Closed
# ======================
@router.put("/operations/{rescue_update_id}/status")
def update_rescue_status(
    rescue_update_id: int,
    payload: StatusUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    allowed_status = [
        "Acknowledged",
        "Rescue In Progress",
        "Controlled",
        "Closed"
    ]

    if payload.status not in allowed_status:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Allowed values: {allowed_status}"
        )

    rescue_update = db.query(RescueUpdate).filter(
        RescueUpdate.id == rescue_update_id
    ).first()

    if not rescue_update:
        raise HTTPException(status_code=404, detail="Rescue operation not found")

    # Rescue team can only update their own operations
    if str(rescue_update.rescue_team_id) != str(current_user.id):
        raise HTTPException(
            status_code=403,
            detail="You can only update your own rescue operations"
        )

    # Must be acknowledged before moving to other statuses
    if not rescue_update.is_acknowledged:
        raise HTTPException(
            status_code=400,
            detail="Report must be acknowledged before updating status"
        )

    rescue_update.status = payload.status # type: ignore
    db.commit()
    db.refresh(rescue_update)

    return {
        "message": "Rescue operation status updated successfully",
        "rescue_update_id": rescue_update.id,
        "new_status": rescue_update.status,
        "updated_by": current_user.email
    }


# ======================
# View My Active Rescue Operations
# Returns empty list (not 404) when no operations found
# ======================
@router.get("/my-operations", response_model=List[dict])
def get_my_operations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    operations = db.query(RescueUpdate).filter(
        RescueUpdate.rescue_team_id == current_user.id
    ).all()

    result = []
    for op in operations:
        report = db.query(Incident).filter(Incident.id == op.incident_id).first()
        result.append({
            "rescue_update_id": op.id,
            "incident_id": op.incident_id,
            "report_title": report.title if report else None,
            "disaster_type": report.disaster_type if report else None,
            "location": report.location if report else None,
            "latitude": report.latitude if report else None,
            "longitude": report.longitude if report else None,
            "severity": report.severity if report else None,
            "description": report.description if report else None,
            "rescue_status": op.status,
            "is_acknowledged": op.is_acknowledged,
            "acknowledged_at": op.acknowledged_at,
            "post_incident_report": op.post_incident_report,
            "media_urls": [m.file_path for m in report.media] if report and report.media else [],
            "created_at": op.created_at,
            "updated_at": op.updated_at
        })

    return result


# ======================
# Submit Post-Incident Report
# Only allowed when operation status is Controlled or Closed
# ======================
@router.post("/operations/{rescue_update_id}/post-incident-report")
def submit_post_incident_report(
    rescue_update_id: int,
    payload: PostIncidentReportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    rescue_update = db.query(RescueUpdate).filter(
        RescueUpdate.id == rescue_update_id
    ).first()

    if not rescue_update:
        raise HTTPException(status_code=404, detail="Rescue operation not found")

    # Only the assigned rescue team member can submit
    if str(rescue_update.rescue_team_id) != str(current_user.id):
        raise HTTPException(
            status_code=403,
            detail="You can only submit reports for your own operations"
        )

    # Post-incident report only allowed after operation is Controlled or Closed
    if rescue_update.status not in ["Controlled", "Closed"]:
        raise HTTPException(
            status_code=400,
            detail="Post-incident report can only be submitted when status is 'Controlled' or 'Closed'"
        )

    rescue_update.post_incident_report = payload.post_incident_report
    rescue_update.post_incident_submitted_at = datetime.utcnow()

    db.commit()
    db.refresh(rescue_update)

    return {
        "message": "Post-incident report submitted successfully",
        "rescue_update_id": rescue_update.id,
        "submitted_by": current_user.email,
        "submitted_at": rescue_update.post_incident_submitted_at
    }
