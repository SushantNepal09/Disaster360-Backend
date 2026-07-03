
# pyrefly: ignore [missing-import]
from app.models.report_media import ReportMedia
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks

# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session

# pyrefly: ignore [missing-import]
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from app.database import get_db
from app.models.incident import Incident
from app.models.incident_assignment import IncidentAssignment
from app.models.report import Report
from app.models.rescue_update import RescueUpdate
from app.models.rescue_timeline_event import RescueTimelineEvent
from app.models.user import User
from app.routes.auth import get_current_rescue_team, get_current_user
from app.services.notification_service import send_push_notification_task, NotificationType
from app.services.status_transition_service import StatusTransitionService
from app.core.statuses import IncidentStatus, ReportStatus, AssignmentStatus

router = APIRouter(prefix="/rescue", tags=["Rescue Team"])


# ======================
# Pydantic Schemas
# ======================
class AcknowledgeRequest(BaseModel):
    incident_id: int


class StatusUpdateRequest(BaseModel):
    status: str  # Accepted | In Progress | Completed | Cancelled


class PostIncidentReportRequest(BaseModel):
    post_incident_report: str

class TimelineEventCreateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    event_type: str = "MANUAL"

class TimelineEventUpdateRequest(BaseModel):
    description: str

# ======================
# Live Situation Update Schema
# ======================
class LiveUpdateCreateRequest(BaseModel):
    category: str
    severity: str
    message: str
    media_url: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    visibility: str = "Public"
    device_time: Optional[datetime] = None

# ======================
# Operation Media Schema
# ======================
class OperationMediaItem(BaseModel):
    url: str
    filename: Optional[str] = None
    size: Optional[int] = None
    title: Optional[str] = None

class OperationMediaCreateRequest(BaseModel):
    media: List[OperationMediaItem]

# ======================
# Get Rescue Team Profile + Stats
# ======================
@router.get("/profile")
def get_rescue_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    """Returns the rescue team member's profile data + mission statistics."""
    all_ops = db.query(IncidentAssignment).filter(
        IncidentAssignment.team_id == current_user.id
    ).all()

    total = len(all_ops)
    active = len([op for op in all_ops if op.status in [AssignmentStatus.ACCEPTED, AssignmentStatus.IN_PROGRESS, AssignmentStatus.ASSIGNED]])
    completed = len([op for op in all_ops if op.status == AssignmentStatus.COMPLETED])

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
    reports = db.query(Incident).filter(
        Incident.status.notin_(["Pending", "Rejected"]),
        Incident.verified == True
    ).all()

    data = []
    for r in reports:
        reporter_name = "Unknown Reporter"
        if r.reports and len(r.reports) > 0:
            # Sort reports by timestamp to ensure we get the earliest (primary) reporter
            sorted_reports = sorted(r.reports, key=lambda rep: getattr(rep, 'timestamp', None) or datetime.max)
            if sorted_reports[0].user:
                reporter_name = sorted_reports[0].user.full_name or sorted_reports[0].user.email or "Unknown Reporter"

        team_assignment = next((a for a in r.assignments if a.team_id == current_user.id), None)

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
            "teamAssignmentStatus": team_assignment.status if team_assignment else None,
            "isAssignedToCurrentTeam": team_assignment is not None,
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
            "sources": r.sources or 1,
            "actions": {
                "canAcknowledge": bool(team_assignment and team_assignment.status == "Assigned"),
                "canUpdateStatus": bool(team_assignment and team_assignment.status == "Accepted"),
                "canSubmitReport": bool(team_assignment and team_assignment.status in ["In Progress", "Completed"]),
                "rescueUpdateId": team_assignment.id if team_assignment else None
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

    # Check if there is an active assignment for this rescue team
    assignment = db.query(IncidentAssignment).filter(
        IncidentAssignment.incident_id == payload.incident_id,
        IncidentAssignment.team_id == current_user.id,
        IncidentAssignment.status == AssignmentStatus.ASSIGNED
    ).first()

    if not assignment:
        raise HTTPException(
            status_code=400,
            detail="You are not assigned to this incident or have already acknowledged it."
        )

    # Accept the assignment via transition service
    StatusTransitionService.change_assignment_status(db, assignment.id, AssignmentStatus.ACCEPTED, current_user.id, "rescue")

    # Add a log entry to RescueUpdate
    rescue_log = RescueUpdate(
        incident_id=payload.incident_id,
        rescue_team_id=current_user.id,
        message="Assignment accepted."
    )
    db.add(rescue_log)
    db.commit()

    return {
        "message": f"Incident {payload.incident_id} acknowledged successfully",
        "assignment_id": assignment.id,
        "acknowledged_by": current_user.email
    }


# ======================
# Update Rescue Operation Status
# Lifecycle: Accepted → In Progress → Completed
# ======================
@router.put("/operations/{assignment_id}/status")
def update_rescue_status(
    assignment_id: int,
    payload: StatusUpdateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    assignment = db.query(IncidentAssignment).filter(
        IncidentAssignment.id == assignment_id
    ).first()

    if not assignment:
        raise HTTPException(status_code=404, detail="Rescue assignment not found")

    # Rescue team can only update their own operations
    if str(assignment.team_id) != str(current_user.id):
        raise HTTPException(
            status_code=403,
            detail="You can only update your own rescue assignments"
        )

    # Use the transition service
    StatusTransitionService.change_assignment_status(db, assignment.id, payload.status, current_user.id, "rescue")
    
    # Add an append-only log entry
    db.add(RescueUpdate(
        incident_id=assignment.incident_id,
        rescue_team_id=current_user.id,
        message=f"Status updated to {payload.status}."
    ))

    db.commit()

    incident = db.query(Incident).filter(Incident.id == assignment.incident_id).first()
    if incident:
        reporter_ids = list(set([str(r.user_id) for r in incident.reports if r.user_id]))
        if reporter_ids:
            background_tasks.add_task(
                send_push_notification_task,
                reporter_ids,
                NotificationType.RESCUE_UPDATE,
                "Rescue Operation Update",
                f"Rescue team status updated to: {payload.status}",
                {"incident_id": str(incident.id)}
            )

    return {
        "message": "Rescue assignment status updated successfully",
        "assignment_id": assignment.id,
        "new_status": payload.status,
        "updated_by": current_user.email
    }


# ======================

# ======================
# Accept a Rescue Assignment
# ======================
@router.put("/assignments/{assignment_id}/accept")
def accept_assignment(
    assignment_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    assignment = db.query(IncidentAssignment).filter(
        IncidentAssignment.id == assignment_id,
        IncidentAssignment.team_id == current_user.id
    ).first()

    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    if assignment.status != AssignmentStatus.ASSIGNED:
        raise HTTPException(status_code=400, detail="Only 'Assigned' assignments can be accepted")

    StatusTransitionService.change_assignment_status(db, assignment.id, AssignmentStatus.ACCEPTED, current_user.id, "rescue", remarks="Accepted by rescue team")
    
    # Add a log entry to RescueUpdate
    db.add(RescueUpdate(
        incident_id=assignment.incident_id,
        rescue_team_id=current_user.id,
        message="Assignment accepted."
    ))

    db.commit()
    
    # Fetch incident for title
    incident = db.query(Incident).filter(Incident.id == assignment.incident_id).first()
    incident_title = incident.title if incident else "Disaster Incident"
    
    # Notify Admins
    admins = db.query(User).filter(User.role == "admin").all()
    admin_ids = [admin.id for admin in admins]
    if admin_ids:
        background_tasks.add_task(
            send_push_notification_task,
            user_ids=admin_ids,
            notification_type=NotificationType.SYSTEM,
            title="Rescue Assignment Accepted",
            body=f"{current_user.name} has accepted the rescue assignment for \"{incident_title}\"."
        )
        
    # Notify Citizens who reported this incident
    reports = db.query(Report).filter(Report.incident_id == assignment.incident_id).all()
    citizen_ids = list(set(r.user_id for r in reports if r.user_id))
    if citizen_ids:
        background_tasks.add_task(
            send_push_notification_task,
            user_ids=citizen_ids,
            notification_type=NotificationType.RESCUE_UPDATE,
            title="Rescue Team En Route",
            body=f"{current_user.name} has accepted your reported disaster and is now responding."
        )

    return {"success": True, "message": "Assignment accepted successfully"}

# ======================
# Reject a Rescue Assignment
# ======================
# pyrefly: ignore [missing-import]
from pydantic import BaseModel, Field
from datetime import datetime

class RejectAssignmentRequest(BaseModel):
    reason: str = Field(..., min_length=5, description="The reason for rejecting the assignment")

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

    if assignment.status != AssignmentStatus.ASSIGNED:
        raise HTTPException(status_code=400, detail="Only 'Assigned' assignments can be rejected")

    StatusTransitionService.change_assignment_status(db, assignment.id, AssignmentStatus.REJECTED, current_user.id, "rescue", remarks=payload.reason)
    assignment.rejection_reason = payload.reason
    assignment.rejected_at = datetime.utcnow()
    
    # Add a log entry to RescueUpdate
    db.add(RescueUpdate(
        incident_id=assignment.incident_id,
        rescue_team_id=current_user.id,
        message=f"Assignment rejected: {payload.reason or 'No reason provided'}"
    ))

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
        IncidentAssignment.team_id == current_user.id,
        IncidentAssignment.status.notin_([AssignmentStatus.CANCELLED, AssignmentStatus.REJECTED, AssignmentStatus.COMPLETED])
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

        can_acknowledge = a.status == AssignmentStatus.ASSIGNED
        can_update_status = a.status in [AssignmentStatus.ACCEPTED, AssignmentStatus.IN_PROGRESS]
        can_submit_report = a.status == AssignmentStatus.COMPLETED and (not rescue_update or not rescue_update.post_incident_report)

        reporter_name = "Unknown Reporter"
        if incident.reports and len(incident.reports) > 0:
            # Sort reports by timestamp to ensure we get the earliest (primary) reporter
            sorted_reports = sorted(incident.reports, key=lambda rep: getattr(rep, 'timestamp', None) or datetime.max)
            if sorted_reports[0].user:
                reporter_name = sorted_reports[0].user.full_name or sorted_reports[0].user.email or "Unknown Reporter"

        data.append({
            "assignmentId": str(a.id),
            "assignmentStatus": a.status,
            "rejectionReason": a.rejection_reason,
            "incidentId": str(incident.id),
            "assignmentId": str(a.id),
            "reporterName": reporter_name,
            "reporterStatus": "Active",
            "title": incident.title,
            "disasterType": incident.disaster_type,
            "description": incident.description,
            "severity": incident.severity,
            "status": a.status,
            "verificationStatus": "Verified",
            "assignedAt": a.assigned_at.isoformat() if a.assigned_at else None,
            "acceptedAt": a.accepted_at.isoformat() if a.accepted_at else None,
            "assignedBy": a.assigned_by.full_name or a.assigned_by.email if a.assigned_by else "System",
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
            "sources": incident.sources or 1,
            "rescueTeam": {
                "id": str(current_user.id),
                "name": current_user.full_name or current_user.email
            },
            "actions": {
                "canAcknowledge": a.status == AssignmentStatus.ASSIGNED,
                "canUpdateStatus": a.status in [AssignmentStatus.ACCEPTED, AssignmentStatus.IN_PROGRESS],
                "canSubmitReport": a.status == AssignmentStatus.COMPLETED
            }
        })

    return {
        "success": True,
        "message": "Assigned tasks fetched successfully",
        "data": data
    }


# ======================
# Submit Post-Incident Report
# Only allowed when operation status is Completed
# ======================
@router.post("/operations/{assignment_id}/post-incident-report")
def submit_post_incident_report(
    assignment_id: int,
    payload: PostIncidentReportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    assignment = db.query(IncidentAssignment).filter(
        IncidentAssignment.id == assignment_id
    ).first()

    if not assignment:
        raise HTTPException(status_code=404, detail="Rescue assignment not found")

    # Only the assigned rescue team member can submit
    if str(assignment.team_id) != str(current_user.id):
        raise HTTPException(
            status_code=403,
            detail="You can only submit reports for your own operations"
        )

    # Post-incident report only allowed after operation is Completed
    if assignment.status != AssignmentStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail="Post-incident report can only be submitted when status is 'Completed'"
        )

    report_log = RescueUpdate(
        incident_id=assignment.incident_id,
        rescue_team_id=current_user.id,
        post_incident_report=payload.post_incident_report,
        message="Post-incident report submitted."
    )
    db.add(report_log)
    db.commit()
    db.refresh(report_log)

    return {
        "message": "Post-incident report submitted successfully",
        "rescue_update_id": report_log.id,
        "submitted_by": current_user.email
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
        IncidentAssignment.team_id == current_user.id,
        IncidentAssignment.status.notin_([AssignmentStatus.CANCELLED, AssignmentStatus.REJECTED, AssignmentStatus.COMPLETED])
    ).all()

    data = []
    for a in assignments:
        incident = a.incident
        if not incident:
            continue

        can_acknowledge = a.status == AssignmentStatus.ASSIGNED

        reporter_name = "Unknown Reporter"
        if incident.reports and len(incident.reports) > 0 and incident.reports[0].user:
            reporter_name = incident.reports[0].user.full_name or incident.reports[0].user.email or "Unknown Reporter"

        data.append({
            "id": incident.id,
            "assignment_id": a.id,
            "user_id": str(incident.reports[0].user_id) if incident.reports and incident.reports[0].user_id else "",
            "submissions": [{"user_name": reporter_name}],
            "disaster_type": incident.disaster_type,
            "title": incident.title,
            "description": incident.description,
            "latitude": incident.latitude,
            "longitude": incident.longitude,
            "severity": incident.severity,
            "status": a.status,
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


# ======================
# View Completed Assignments
# ======================
@router.get("/completed-assignments")
def get_completed_assignments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    assignments = db.query(IncidentAssignment).filter(
        IncidentAssignment.team_id == current_user.id,
        IncidentAssignment.status == AssignmentStatus.COMPLETED
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

        reporter_name = "Unknown Reporter"
        if incident.reports and len(incident.reports) > 0 and incident.reports[0].user:
            reporter_name = incident.reports[0].user.full_name or incident.reports[0].user.email or "Unknown Reporter"

        data.append({
            "assignmentId": str(a.id),
            "assignmentStatus": a.status,
            "rejectionReason": a.rejection_reason,
            "incidentId": str(incident.id),
            "reporterName": reporter_name,
            "reporterStatus": "Active",
            "title": incident.title,
            "disasterType": incident.disaster_type,
            "description": incident.description,
            "severity": incident.severity,
            "status": a.status,
            "verificationStatus": "Verified",
            "assignedAt": a.assigned_at.isoformat() if a.assigned_at else None,
            "completedAt": a.completed_at.isoformat() if hasattr(a, 'completed_at') and a.completed_at else None,
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
                "canAcknowledge": False,
                "canUpdateStatus": False,
                "canSubmitReport": not rescue_update or not rescue_update.post_incident_report
            },
            "postIncidentReport": rescue_update.post_incident_report if rescue_update else None
        })

    # Sort by completed time (newest first) if available, else assigned_at
    data.sort(key=lambda x: x["completedAt"] or x["assignedAt"] or "", reverse=True)

    return {
        "success": True,
        "message": "Completed tasks fetched successfully",
        "data": data
    }


# ======================
# Post Live Situation Update
# ======================
@router.post("/incidents/{incident_id}/live-updates")
def post_live_update(
    incident_id: int,
    payload: LiveUpdateCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    from app.models.rescue_live_update import RescueLiveUpdate
    from app.models.incident_assignment import IncidentAssignment
    
    # 1. Ensure team is assigned and status is In Progress
    assignment = db.query(IncidentAssignment).filter(
        IncidentAssignment.incident_id == incident_id,
        IncidentAssignment.team_id == current_user.id
    ).first()
    
    if not assignment:
        raise HTTPException(status_code=403, detail="Not assigned to this incident.")
        
    if assignment.status != AssignmentStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=400, 
            detail="Live updates can only be posted while the operation is In Progress."
        )
        
    # 2. Rate Limiting Check
    last_update = db.query(RescueLiveUpdate).filter(
        RescueLiveUpdate.assignment_id == assignment.id
    ).order_by(RescueLiveUpdate.created_at.desc()).first()
    
    if last_update:
        time_since_last = (datetime.utcnow() - last_update.created_at).total_seconds()
        if time_since_last < 10:
            raise HTTPException(status_code=429, detail="Please wait before posting another update.")
            
    # 3. Create Update
    live_update = RescueLiveUpdate(
        incident_id=incident_id,
        assignment_id=assignment.id,
        team_id=current_user.id,
        team_name=current_user.full_name or current_user.email or "Rescue Team",
        category=payload.category,
        severity=payload.severity,
        message=payload.message,
        media_url=payload.media_url,
        latitude=payload.latitude,
        longitude=payload.longitude,
        visibility=payload.visibility,
        device_time=payload.device_time
    )
    
    db.add(live_update)
    db.commit()
    db.refresh(live_update)
    
    # Trigger notifications using existing task
    send_push_notification_task(
        db,
        title="Live Rescue Update",
        body=f"[{payload.category}] {payload.message}",
        data={"incident_id": str(incident_id), "type": "LIVE_UPDATE"},
        notification_type=NotificationType.DISASTER_UPDATE
    )
    
    return {
        "success": True,
        "message": "Live update posted successfully",
        "data": {
            "id": live_update.id
        }
    }


# ======================
# Timeline API (Phase 8)
# ======================
@router.get("/operations/{operation_id}/timeline")
def get_timeline_events(
    operation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    # Verify active operation
    assignment = db.query(IncidentAssignment).filter(
        IncidentAssignment.incident_id == operation_id,
        IncidentAssignment.team_id == current_user.id
    ).first()
    
    if not assignment:
        raise HTTPException(status_code=403, detail="Not assigned to this incident.")

    events = db.query(RescueTimelineEvent).filter(
        RescueTimelineEvent.incident_id == operation_id
    ).order_by(RescueTimelineEvent.created_at.desc()).all()

    media_ids = [e.media_id for e in events if e.media_id is not None]
    media_dict = {}
    if media_ids:
        media_records = db.query(ReportMedia).filter(ReportMedia.id.in_(media_ids)).all()
        media_dict = {m.id: m for m in media_records}

    result = []
    for e in events:
        team_name = None
        if e.team_id:
            team = db.query(User).filter(User.id == e.team_id).first()
            if team:
                team_name = team.full_name or team.email

        display_title = e.title
        if e.event_type == "MEDIA_UPLOADED" and e.media_id:
            m = media_dict.get(e.media_id)
            if m:
                actual_title = m.title if m.title else f"Image {e.media_id}"
                display_title = f"📷 {actual_title} (Image)"

        result.append({
            "id": e.id,
            "incident_id": e.incident_id,
            "assignment_id": e.assignment_id,
            "team_id": str(e.team_id) if e.team_id else None,
            "team_name": team_name,
            "created_by": str(e.created_by) if e.created_by else None,
            "event_type": e.event_type,
            "title": display_title,
            "description": e.description,
            "media_id": e.media_id,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "metadata_json": e.metadata_json,
            "is_system_generated": e.is_system_generated,
            "updated_at": e.updated_at.isoformat() if e.updated_at else None,
            "updated_by": str(e.updated_by) if e.updated_by else None,
            "is_edited": e.is_edited
        })

    return {
        "success": True,
        "data": result
    }

@router.post("/operations/{operation_id}/timeline")
def create_manual_timeline_event(
    operation_id: int,
    payload: TimelineEventCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    # Verify active operation
    assignment = db.query(IncidentAssignment).filter(
        IncidentAssignment.incident_id == operation_id,
        IncidentAssignment.team_id == current_user.id
    ).first()
    
    if not assignment:
        raise HTTPException(status_code=403, detail="Not assigned to this incident.")
        
    if assignment.status != AssignmentStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=400, 
            detail="Manual updates can only be posted while the operation is In Progress."
        )

    new_event = RescueTimelineEvent(
        incident_id=operation_id,
        assignment_id=assignment.id,
        team_id=current_user.id,
        created_by=current_user.id,
        event_type=payload.event_type,
        title=payload.title,
        description=payload.description,
        is_system_generated=False,
        created_at=datetime.utcnow()
    )
    
    db.add(new_event)
    db.commit()
    db.refresh(new_event)

    return {
        "success": True,
        "message": "Timeline event added successfully.",
        "data": {
            "id": new_event.id,
            "created_at": new_event.created_at.isoformat()
        }
    }


@router.put("/operations/timeline/{timeline_event_id}")
def update_timeline_event(
    timeline_event_id: int,
    payload: TimelineEventUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role not in ["rescue", "admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to access timeline.")
        
    event = db.query(RescueTimelineEvent).filter(RescueTimelineEvent.id == timeline_event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Timeline event not found.")

    if current_user.role != "admin" and str(event.created_by) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized to edit this timeline event.")

    new_description = payload.description.strip()
    if not new_description:
        raise HTTPException(status_code=400, detail="Description cannot be empty.")

    team_name = None
    if event.team_id:
        team = db.query(User).filter(User.id == event.team_id).first()
        if team:
            team_name = team.full_name or team.email

    current_description = (event.description or "").strip()
    if new_description == current_description:
        return {
            "id": event.id,
            "incident_id": event.incident_id,
            "assignment_id": event.assignment_id,
            "team_id": str(event.team_id) if event.team_id else None,
            "team_name": team_name,
            "created_by": str(event.created_by) if event.created_by else None,
            "event_type": event.event_type,
            "title": event.title,
            "description": event.description,
            "created_at": event.created_at.isoformat() if event.created_at else None,
            "metadata_json": event.metadata_json,
            "is_system_generated": event.is_system_generated,
            "updated_at": event.updated_at.isoformat() if event.updated_at else None,
            "updated_by": str(event.updated_by) if event.updated_by else None,
            "is_edited": event.is_edited
        }

    event.description = new_description
    event.updated_at = datetime.utcnow()
    event.updated_by = current_user.id
    event.is_edited = True

    db.commit()
    db.refresh(event)

    return {
        "id": event.id,
        "incident_id": event.incident_id,
        "assignment_id": event.assignment_id,
        "team_id": str(event.team_id) if event.team_id else None,
        "team_name": team_name,
        "created_by": str(event.created_by) if event.created_by else None,
        "event_type": event.event_type,
        "title": event.title,
        "description": event.description,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "metadata_json": event.metadata_json,
        "is_system_generated": event.is_system_generated,
        "updated_at": event.updated_at.isoformat() if event.updated_at else None,
        "updated_by": str(event.updated_by) if event.updated_by else None,
        "is_edited": event.is_edited
    }


@router.delete("/operations/timeline/{timeline_event_id}")
def delete_timeline_event(
    timeline_event_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    from fastapi.responses import Response
    
    if current_user.role not in ["rescue", "admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to access timeline.")
        
    event = db.query(RescueTimelineEvent).filter(RescueTimelineEvent.id == timeline_event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Timeline event not found.")

    if current_user.role != "admin" and str(event.created_by) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized to delete this timeline event.")

    db.delete(event)
    db.commit()

    return Response(status_code=204)

@router.post("/operations/{operation_id}/media")
def upload_operation_media(
    operation_id: int,
    payload: OperationMediaCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_rescue_team)
):
    assignment = db.query(IncidentAssignment).filter(
        IncidentAssignment.incident_id == operation_id,
        IncidentAssignment.team_id == current_user.id
    ).first()
    
    if not assignment:
        raise HTTPException(status_code=403, detail="Not assigned to this incident.")
        
    created_media = []
    
    for item in payload.media:
        new_media = ReportMedia(
            user_id=current_user.id,
            incident_id=operation_id,
            assignment_id=assignment.id,
            file_path=item.url,
            file_type="image",
            original_filename=item.filename,
            title=item.title,
            file_size=item.size,
            created_at=datetime.utcnow()
        )
        db.add(new_media)
        db.flush()
        
        # Automatically generate timeline event
        new_event = RescueTimelineEvent(
            incident_id=operation_id,
            assignment_id=assignment.id,
            team_id=current_user.id,
            created_by=current_user.id,
            event_type="MEDIA_UPLOADED",
            title=item.title if item.title else "Image Uploaded",
            description=None,
            media_id=new_media.id,
            is_system_generated=True,
            created_at=datetime.utcnow()
        )
        db.add(new_event)
        
        created_media.append(new_media)
        
    db.commit()
    
    return {
        "success": True,
        "message": f"Successfully attached {len(payload.media)} media files.",
        "data": [{"id": m.id, "url": m.file_path} for m in created_media]
    }


@router.get("/operations/{operation_id}/media")
def get_operation_media(
    operation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role not in ["rescue", "admin"]:
        raise HTTPException(status_code=403, detail="Not authorized.")
        
    media_files = db.query(ReportMedia).filter(
        ReportMedia.incident_id == operation_id,
        ReportMedia.assignment_id != None
    ).order_by(ReportMedia.created_at.asc()).all()
    
    result = []
    for m in media_files:
        result.append({
            "id": m.id,
            "incident_id": m.incident_id,
            "assignment_id": m.assignment_id,
            "user_id": str(m.user_id),
            "file_path": m.file_path,
            "original_filename": m.original_filename,
            "title": m.title,
            "file_size": m.file_size,
            "created_at": m.created_at.isoformat() if m.created_at else None
        })
        
    return {
        "success": True,
        "data": result
    }
