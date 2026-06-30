
# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks

# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session

# pyrefly: ignore [missing-import]
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from ..database import get_db
from ..models.incident import Incident
from ..models.incident_assignment import IncidentAssignment
from ..models.rescue_update import RescueUpdate, RescueUpdateStatus
from ..models.user import User
from .auth import get_current_rescue_team
from ..services.notification_service import send_push_notification_task, NotificationType

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
# ======================
@router.get("/all-reports")
def get_all_reports(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    reports = db.query(Incident).filter(Incident.status.in_(["Verified", "Assigned"])).all()

    data = []
    for r in reports:
        reporter_name = "Unknown Reporter"
        if r.reports and len(r.reports) > 0 and r.reports[0].user:
            reporter_name = r.reports[0].user.full_name or r.reports[0].user.email or "Unknown Reporter"

        data.append({
            "incidentId": str(r.id),
            "reporterName": reporter_name,
            "reporterStatus": "Active",
            "title": r.title,
            "disasterType": r.disaster_type,
            "description": r.description,
            "severity": r.severity,
            "status": r.status,
            "verificationStatus": "Verified",
            "reportedAt": r.created_at.isoformat() if r.created_at else None,
            "location": {
                "address": r.location,
                "latitude": r.latitude,
                "longitude": r.longitude
            },
            "media": [
                {
                    "id": f"MED-{m.id}",
                    "type": m.file_type,
                    "url": m.file_path
                } for m in r.media
            ] if hasattr(r, "media") and r.media else [],
            "actions": {
                "canAssign": False,
                "canViewDetails": True
            }
        })

    return {
        "success": True,
        "message": "All rescue reports fetched successfully",
        "data": data
    }


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

    if report.status not in ["Verified", "Assigned"]:
        raise HTTPException(
            status_code=400,
            detail="Only verified or assigned incidents can be acknowledged"
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
        status=RescueUpdateStatus.acknowledged,
        is_acknowledged=True,
        acknowledged_at=datetime.utcnow()
    )

    # Update parent incident status
    report.status = "Acknowledged"
    
    db.add(rescue_update)
    db.commit()
    db.refresh(rescue_update)
    db.refresh(report)

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
    background_tasks: BackgroundTasks,
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

    incident = db.query(Incident).filter(Incident.id == rescue_update.incident_id).first()
    if incident:
        reporter_ids = list(set([str(r.user_id) for r in incident.reports if r.user_id]))
        if reporter_ids:
            background_tasks.add_task(
                send_push_notification_task,
                reporter_ids,
                NotificationType.RESCUE_UPDATE,
                "Rescue Operation Update",
                f"Rescue team status updated to: {rescue_update.status}",
                {"incident_id": str(incident.id)}
            )

    return {
        "message": "Rescue operation status updated successfully",
        "rescue_update_id": rescue_update.id,
        "new_status": rescue_update.status,
        "updated_by": current_user.email
    }


# ======================

# ======================
# Accept a Rescue Assignment
# ======================
@router.put("/assignments/{assignment_id}/accept")
def accept_assignment(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    assignment = db.query(IncidentAssignment).filter(
        IncidentAssignment.id == assignment_id,
        IncidentAssignment.team_id == current_user.id
    ).first()

    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    if assignment.status != "Assigned":
        raise HTTPException(status_code=400, detail="Only 'Assigned' assignments can be accepted")

    assignment.status = "Accepted"
    assignment.accepted_at = datetime.utcnow()
    
    # Check if RescueUpdate already exists for this team/incident
    existing_update = db.query(RescueUpdate).filter(
        RescueUpdate.incident_id == assignment.incident_id,
        RescueUpdate.rescue_team_id == current_user.id
    ).first()

    if not existing_update:
        rescue_update = RescueUpdate(
            incident_id=assignment.incident_id,
            rescue_team_id=current_user.id,
            status=RescueUpdateStatus.acknowledged,
            is_acknowledged=True,
            acknowledged_at=datetime.utcnow()
        )
        db.add(rescue_update)

    incident = assignment.incident
    if incident.status != "Rescue In Progress":
        incident.status = "Rescue In Progress"

    db.commit()
    return {"success": True, "message": "Assignment accepted successfully"}

# ======================
# Reject a Rescue Assignment
# ======================
from pydantic import BaseModel

class RejectAssignmentRequest(BaseModel):
    reason: str = None

@router.put("/assignments/{assignment_id}/reject")
def reject_assignment(
    assignment_id: int,
    payload: RejectAssignmentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    assignment = db.query(IncidentAssignment).filter(
        IncidentAssignment.id == assignment_id,
        IncidentAssignment.team_id == current_user.id
    ).first()

    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    if assignment.status != "Assigned":
        raise HTTPException(status_code=400, detail="Only 'Assigned' assignments can be rejected")

    assignment.status = "Rejected"
    assignment.rejected_at = datetime.utcnow()
    assignment.rejection_reason = payload.reason
    
    incident = assignment.incident
    
    all_assignments = db.query(IncidentAssignment).filter(IncidentAssignment.incident_id == incident.id).all()
    # Check if all assignments are rejected
    all_rejected = all(a.status == "Rejected" for a in all_assignments)
    
    if all_rejected:
        incident.status = "Verified"
        
    db.commit()
    return {"success": True, "message": "Assignment rejected successfully"}


# ======================
# View My Assigned Tasks
# ======================
@router.get("/my-assignments")
def get_my_assignments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    assignments = db.query(IncidentAssignment).filter(
        IncidentAssignment.team_id == current_user.id
    ).all()

    data = []
    for a in assignments:
        incident = a.incident
        if not incident:
            continue

        rescue_update = db.query(RescueUpdate).filter(
            RescueUpdate.incident_id == incident.id,
            RescueUpdate.rescue_team_id == current_user.id
        ).first()

        status = incident.status
        if rescue_update:
            status = rescue_update.status

        can_acknowledge = not rescue_update or not rescue_update.is_acknowledged
        can_update_status = rescue_update is not None and rescue_update.is_acknowledged and rescue_update.status not in ["Controlled", "Closed"]
        can_submit_report = rescue_update is not None and rescue_update.status in ["Controlled", "Closed"] and not rescue_update.post_incident_report

        reporter_name = "Unknown Reporter"
        if incident.reports and len(incident.reports) > 0 and incident.reports[0].user:
            reporter_name = incident.reports[0].user.full_name or incident.reports[0].user.email or "Unknown Reporter"

        data.append({
            "assignmentId": str(a.id),
            "assignmentStatus": a.status,
            "rejectionReason": a.rejection_reason,
            "incidentId": str(incident.id),
            "rescueUpdateId": str(rescue_update.id) if rescue_update else None,
            "reporterName": reporter_name,
            "reporterStatus": "Active",
            "title": incident.title,
            "disasterType": incident.disaster_type,
            "description": incident.description,
            "severity": incident.severity,
            "status": status,
            "verificationStatus": "Verified",
            "assignedAt": a.assigned_at.isoformat() if a.assigned_at else None,
            "reportedAt": incident.created_at.isoformat() if incident.created_at else None,
            "location": {
                "address": incident.location,
                "latitude": incident.latitude,
                "longitude": incident.longitude
            },
            "media": [
                {
                    "id": f"MED-{m.id}",
                    "type": m.file_type,
                    "url": m.file_path
                } for m in incident.media
            ] if hasattr(incident, "media") and incident.media else [],
            "rescueTeam": {
                "id": str(current_user.id),
                "name": current_user.full_name or current_user.email
            },
            "actions": {
                "canAcknowledge": can_acknowledge,
                "canUpdateStatus": can_update_status,
                "canSubmitReport": can_submit_report
            }
        })

    return {
        "success": True,
        "message": "Assigned tasks fetched successfully",
        "data": data
    }


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


# ======================
# View Home Feed (Assigned Reports formatted for shared UI)
# ======================
@router.get("/home")
def get_rescue_home_feed(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    assignments = db.query(IncidentAssignment).filter(
        IncidentAssignment.team_id == current_user.id
    ).all()

    data = []
    for a in assignments:
        incident = a.incident
        if not incident:
            continue

        rescue_update = db.query(RescueUpdate).filter(
            RescueUpdate.incident_id == incident.id,
            RescueUpdate.rescue_team_id == current_user.id
        ).first()

        status = incident.status
        if rescue_update:
            status = rescue_update.status

        can_acknowledge = not rescue_update or not rescue_update.is_acknowledged

        reporter_name = "Unknown Reporter"
        if incident.reports and len(incident.reports) > 0 and incident.reports[0].user:
            reporter_name = incident.reports[0].user.full_name or incident.reports[0].user.email or "Unknown Reporter"

        data.append({
            "id": incident.id,
            "user_id": str(incident.reports[0].user_id) if incident.reports and incident.reports[0].user_id else "",
            "submissions": [{"user_name": reporter_name}],
            "disaster_type": incident.disaster_type,
            "title": incident.title,
            "description": incident.description,
            "latitude": incident.latitude,
            "longitude": incident.longitude,
            "severity": incident.severity,
            "status": status,
            "verified": True,
            "likes": 0,
            "dislikes": 0,
            "user_reaction": None,
            "created_at": incident.created_at.isoformat() if incident.created_at else "",
            "media_urls": [m.file_path for m in incident.media] if hasattr(incident, "media") and incident.media else [],
            "rescue_team": current_user.full_name or current_user.email,
            "is_accepted": not can_acknowledge
        })

    # Sort by assigned/created time (newest first)
    data.sort(key=lambda x: x["created_at"], reverse=True)

    return data
