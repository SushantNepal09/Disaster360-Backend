# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session

# pyrefly: ignore [missing-import]
from pydantic import BaseModel
from typing import Optional, List

# pyrefly: ignore [missing-import]
from app.database import get_db
from app.models.incident import Incident
from app.models.incident_assignment import IncidentAssignment
from app.models.report import Report
from app.models.report_reaction import ReportReaction
from app.models.user import User
from app.routes.auth import get_current_admin
from datetime import datetime, timedelta, timezone

from app.services.geo_service import get_users_to_notify
from app.services.notification_service import send_push_notification_task, NotificationType
from app.services.status_transition_service import StatusTransitionService
from app.core.statuses import IncidentStatus, ReportStatus, AssignmentStatus

router = APIRouter(prefix="/admin", tags=["Admin"])


# ======================
# Pydantic Schemas
# ======================
class AssignTeamRequest(BaseModel):
    team_ids: list[str] = []


class StatusUpdateRequest(BaseModel):
    status: str


class ReportUpdateRequest(BaseModel):
    disaster_type: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    severity: Optional[str] = None



# pyrefly: ignore [missing-import]
from sqlalchemy.orm import joinedload

# ======================
# Get All Reports (approved admin only)
# Fixed path — must stay above /reports/{report_id}
# ======================
@router.get("/reports")
def get_all_reports(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    from app.routes.reports import serialize_incident
    
    incidents = (
        db.query(Incident)
        .options(
            joinedload(Incident.reports).joinedload(Report.user),
            joinedload(Incident.reactions)
        )
        .all()
    )
    
    result = [serialize_incident(inc, current_user.id, is_admin=True) for inc in incidents]
    return {"total": len(result), "reports": result}


# ======================
# Update Any Report (approved admin only)
# Must stay above /reports/{report_id}/verify and /reports/{report_id}/status
# ======================
@router.put("/reports/{report_id}")
def admin_update_report(
    report_id: int,
    payload: ReportUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    incident = db.query(Incident).filter(Incident.id == report_id).first()

    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Only update fields that were actually sent
    if payload.disaster_type is not None:
        incident.disaster_type = payload.disaster_type
    if payload.title is not None:
        incident.title = payload.title
    if payload.description is not None:
        incident.description = payload.description
    if payload.latitude is not None:
        incident.latitude = payload.latitude
    if payload.longitude is not None:
        incident.longitude = payload.longitude
    if payload.severity is not None:
        incident.severity = payload.severity

    db.commit()
    db.refresh(incident)

    return {
        "message": "Incident updated successfully",
        "report_id": incident.id,
        "updated_by": current_user.email
    }


# ======================
# Verify Report (approved admin only)
# The frontend sends the INCIDENT id (displayed as RPT-{incident.id}).
# We look up the Incident and mark it + all its linked Reports as Verified.
# ======================
@router.put("/reports/{report_id}/verify")
def verify_report(
    report_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    # report_id here is actually the incident id sent from the frontend
    incident = db.query(Incident).filter(Incident.id == report_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Report not found")

    # Mark the incident as verified
    StatusTransitionService.change_incident_status(db, incident.id, IncidentStatus.VERIFIED, current_user.id, "admin", remarks="Admin manually verified")

    db.commit()
    db.refresh(incident)

    # Trigger Admin Verification Notification
    reporter_ids = list(set([str(r.user_id) for r in incident.reports if r.user_id]))
    DISASTER_RADIUS_KM = {"flood": 5.0, "landslide": 3.0, "earthquake": 50.0, "fire": 2.0, "default": 5.0}
    radius_km = DISASTER_RADIUS_KM.get(incident.disaster_type.lower() if incident.disaster_type else "default", 5.0)
    
    incident_local_unit = None
    if incident.location:
        parts = incident.location.split(",")
        incident_local_unit = parts[-1].strip() if parts else incident.location.strip()
    
    nearby_users = get_users_to_notify(db, incident.latitude, incident.longitude, radius_km, incident_local_unit) if incident.latitude and incident.longitude else []
    nearby_user_ids = [str(u.id) for u in nearby_users]
    target_users = list(set(reporter_ids + nearby_user_ids))
    
    if target_users:
        background_tasks.add_task(
            send_push_notification_task,
            target_users,
            NotificationType.INCIDENT_VERIFIED,
            "Incident Verified",
            f"A {incident.disaster_type} in your area has been confirmed.",
            {"incident_id": str(incident.id)}
        )

    return {
        "message": f"Incident {report_id} verified successfully",
        "verified_by": current_user.email
    }


# ======================
# Update Child Report Status (approved admin only)
# ======================
@router.put("/submissions/{sub_id}/status")
def update_submission_status(
    sub_id: int,
    payload: StatusUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    valid_statuses = ["Pending", "Verified", "Rejected", "Resolved"]
    if payload.status not in valid_statuses:
        raise HTTPException(status_code=400, detail="Invalid status")

    sub = db.query(Report).filter(Report.id == sub_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")

    StatusTransitionService.change_report_status(db, sub.id, payload.status, current_user.id, "admin")
    db.commit()

    return {"message": f"Submission status updated to {payload.status}"}


# ======================
# Unverify Report (undo a mistaken verification)
# ======================
@router.put("/reports/{report_id}/unverify")
def unverify_report(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    incident = db.query(Incident).filter(Incident.id == report_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Report not found")

    StatusTransitionService.change_incident_status(db, incident.id, IncidentStatus.PENDING, current_user.id, "admin", remarks="Admin unverified")

    db.commit()
    db.refresh(incident)

    return {
        "message": f"Incident {report_id} unverified (reset to Pending)",
        "unverified_by": current_user.email
    }


# ======================
# Update Report Status (approved admin only)
# ======================
@router.put("/reports/{report_id}/status")
def update_report_status(
    report_id: int,
    payload: StatusUpdateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    allowed_status = [
        "Pending",
        "Verified",
        "Assigned",
        "Verified Rescue In Progress",
        "Verified Controlled",
        "Verified and Closed"
    ]

    incident = db.query(Incident).filter(Incident.id == report_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    # The service automatically validates the transition
    StatusTransitionService.change_incident_status(
        db, incident.id, payload.status, current_user.id, "admin", remarks="Admin status update"
    )

    db.commit()
    db.refresh(incident)

    if payload.status == IncidentStatus.CLOSED:
        DISASTER_RADIUS_KM = {"flood": 5.0, "landslide": 3.0, "earthquake": 50.0, "fire": 2.0, "default": 5.0}
        radius_km = DISASTER_RADIUS_KM.get(incident.disaster_type.lower() if incident.disaster_type else "default", 5.0)
        nearby_users = get_users_to_notify(db, incident.latitude, incident.longitude, radius_km) if incident.latitude and incident.longitude else []
        user_ids = [str(u.id) for u in nearby_users]
        if user_ids:
            background_tasks.add_task(
                send_push_notification_task,
                user_ids,
                NotificationType.INCIDENT_CLOSED,
                "Incident Resolved",
                "The area is now safe. The disaster has been resolved.",
                {"incident_id": str(incident.id)}
            )

    return {
        "message": "Incident status updated successfully",
        "report_id": incident.id,
        "new_status": incident.status,
        "updated_by": current_user.email
    }


# ======================
# Assign Teams to Report (approved admin only)
# ======================
@router.post("/reports/{report_id}/assign")
def admin_assign_team(
    report_id: int,
    payload: AssignTeamRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    incident = db.query(Incident).filter(Incident.id == report_id).first()

    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    if incident.status == IncidentStatus.PENDING:
        raise HTTPException(status_code=400, detail="Pending reports cannot be assigned to rescue teams")

    # Soft Cancel missing active assignments instead of deleting them
    existing_assignments = db.query(IncidentAssignment).filter(IncidentAssignment.incident_id == incident.id).all()
    payload_team_ids = [str(t) for t in (payload.team_ids or [])]
    
    for a in existing_assignments:
        # Treat None as active in case of rogue legacy data
        if str(a.team_id) not in payload_team_ids and (a.status in [AssignmentStatus.ASSIGNED, AssignmentStatus.ACCEPTED, AssignmentStatus.IN_PROGRESS] or a.status is None):
            StatusTransitionService.change_assignment_status(db, a.id, AssignmentStatus.CANCELLED, current_user.id, "admin", remarks="Unassigned by admin")
            
    assigned_users = []
    if payload.team_ids:
        assigned_users = db.query(User).filter(User.id.in_(payload.team_ids)).all()
        
    for u in assigned_users:
        # Check if already assigned actively
        already_active = any(str(a.team_id) == str(u.id) and (a.status in [AssignmentStatus.ASSIGNED, AssignmentStatus.ACCEPTED, AssignmentStatus.IN_PROGRESS] or a.status is None) for a in existing_assignments)
        if not already_active:
            new_assign = IncidentAssignment(
                incident_id=incident.id, 
                team_name=u.full_name or u.email, 
                team_id=u.id,
                status=AssignmentStatus.ASSIGNED
            )
            db.add(new_assign)
            db.flush()
            StatusTransitionService._record_history(db, 'Assignment', new_assign.id, None, AssignmentStatus.ASSIGNED, current_user.id, "Admin assigned team")

    # The Transition Service will handle syncing Incident status automatically via change_assignment_status
    # For newly created assignments, we manually trigger the sync:
    StatusTransitionService._update_derived_statuses(db, incident.id, current_user.id, "admin")

    db.commit()
    db.refresh(incident)

    reporter_ids = list(set([str(r.user_id) for r in incident.reports if r.user_id]))
    if reporter_ids:
        background_tasks.add_task(
            send_push_notification_task,
            reporter_ids,
            NotificationType.RESCUE_ASSIGNED,
            "Rescue Team Assigned",
            "A rescue team has been assigned to your reported incident.",
            {"incident_id": str(incident.id)}
        )

    return {
        "message": "Teams assigned successfully",
        "report_id": incident.id,
        "assigned_teams": payload.team_ids,
        "assigned_by": current_user.email
    }


# ======================
# Delete Any Report (approved admin only)
# ======================
@router.delete("/reports/{report_id}")
def admin_delete_report(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    incident = db.query(Incident).filter(Incident.id == report_id).first()

    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    db.delete(incident)
    db.commit()

    return {
        "message": f"Incident {report_id} has been permanently deleted",
        "deleted_by": current_user.email
    }

# ======================
# Reject Any Report (approved admin only)
# ======================
@router.put("/reports/{report_id}/reject")
def admin_reject_report(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    incident = db.query(Incident).filter(Incident.id == report_id).first()

    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    StatusTransitionService.change_incident_status(db, incident.id, IncidentStatus.REJECTED, current_user.id, "admin", remarks="Rejected by admin")

    db.commit()

    return {
        "message": f"Incident {report_id} has been rejected",
        "rejected_by": current_user.email
    }

# ======================
# Undo Reject Report
# ======================
@router.put("/reports/{report_id}/undo-reject")
def admin_undo_reject_report(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    incident = db.query(Incident).filter(Incident.id == report_id).first()

    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    StatusTransitionService.change_incident_status(db, incident.id, IncidentStatus.PENDING, current_user.id, "admin", remarks="Undo reject by admin")
    # Restore linked reports that were auto-rejected
    for r in incident.reports:
        if r.status == ReportStatus.REJECTED:
            StatusTransitionService.change_report_status(db, r.id, ReportStatus.PENDING, current_user.id, "admin", remarks="Auto-restored because incident was restored")

    db.commit()

    return {
        "message": f"Incident {report_id} is now Pending again",
        "restored_by": current_user.email
    }

# ======================
# Get Active Rescue Operations (approved admin only)
# ======================
@router.get("/active-rescues")
def get_active_rescues(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    from app.models.incident_assignment import IncidentAssignment
    from app.core.statuses import AssignmentStatus
    
    operations = db.query(IncidentAssignment).filter(
        IncidentAssignment.status.notin_([
            AssignmentStatus.COMPLETED,
            AssignmentStatus.CANCELLED,
            AssignmentStatus.REJECTED
        ])
    ).all()
    
    result = []
    for op in operations:
        incident = db.query(Incident).filter(Incident.id == op.incident_id).first()
        team_user = db.query(User).filter(User.id == op.team_id).first()
        
        result.append({
            "initials": "".join([part[0] for part in team_user.full_name.split()[:2]]).upper() if team_user and team_user.full_name else "RT",
            "name": team_user.full_name if team_user else "Unknown Team",
            "locationStatus": f"{incident.location if incident and incident.location else 'Unknown'} — {op.status}",
            "badge": "Active" if op.status in [AssignmentStatus.ACCEPTED, AssignmentStatus.IN_PROGRESS] else "Dispatch",
            "reportType": incident.disaster_type if incident else "Unknown",
            "title": incident.title if incident else "Unknown",
            "flag": op.status
        })
    return result


# ======================
# Get Duplicate Reports (approved admin only)
# ======================
@router.get("/duplicate-reports")
def get_duplicate_reports(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    incidents = db.query(Incident).filter(Incident.sources > 1).all()
    result = []
    for inc in incidents:
        reports_info = []
        for r in inc.reports:
            user = db.query(User).filter(User.id == r.user_id).first()
            child_title = r.title if getattr(r, 'title', None) else f"{inc.disaster_type} — {inc.location if inc.location else 'Unknown'}"
            reports_info.append({
                "id": f"RPT-{r.id:05d}",
                "intId": r.id,
                "title": child_title,
                "date": r.timestamp.strftime("%b %d, %Y") if getattr(r, "timestamp", None) else inc.created_at.strftime("%b %d, %Y"),
                "reporter": user.full_name if user else "Unknown",
                "status": r.status
            })
        
        summary_ids = " & ".join([f"#{r['id']}" for r in reports_info[:2]])
        if len(reports_info) > 2:
            summary_ids += f" & {len(reports_info) - 2} more"
            
        result.append({
            "summary": f"{len(inc.reports)} duplicates merged — {summary_ids}",
            "detail": f"{inc.disaster_type} · {inc.location if inc.location else 'Unknown'}",
            "mergedReports": reports_info
        })
    return result

# ======================
# User Management Endpoints
# ======================

@router.get("/users")
def get_users(
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):
    users = db.query(User).all()
    return [{"id": str(u.id), "full_name": u.full_name, "email": u.email, "phone": u.phone, 
             "role": u.role, "is_admin": u.is_admin, "is_rescueteam": u.is_rescueteam,
             "specialization": u.specialization,
             "created_at": u.created_at} for u in users]


@router.put("/users/{user_id}/status")
def update_user_status(
    user_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if "is_admin" in payload:
        user.is_admin = payload["is_admin"]
    if "is_rescueteam" in payload:
        user.is_rescueteam = payload["is_rescueteam"]
        
    db.commit()
    db.refresh(user)
    return {"message": "User status updated", "is_admin": user.is_admin, "is_rescueteam": user.is_rescueteam}

# ======================
# Analytics Endpoint
# ======================
@router.get("/analytics")
def get_analytics(
    time_range: str = "7D",
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):
    now = datetime.utcnow()
    if time_range == "24H":
        cutoff = now - timedelta(hours=24)
        trend_curr_start = now - timedelta(hours=1)
        trend_prev_start = now - timedelta(hours=2)
    elif time_range == "30D":
        cutoff = now - timedelta(days=30)
        trend_curr_start = now - timedelta(days=7)
        trend_prev_start = now - timedelta(days=14)
    elif time_range == "1Y":
        cutoff = now - timedelta(days=365)
        trend_curr_start = now - timedelta(days=30)
        trend_prev_start = now - timedelta(days=60)
    else:  # default 7D
        cutoff = now - timedelta(days=7)
        trend_curr_start = now - timedelta(days=1)
        trend_prev_start = now - timedelta(days=2)

    # 1. KPIs
    reports = db.query(Report).filter(Report.timestamp >= cutoff).all()
    kpis = {
        "total": len(reports),
        "verified": sum(1 for r in reports if r.verified),
        "rejected": sum(1 for r in reports if r.status == "Rejected"),
        "pending": sum(1 for r in reports if r.status == "Pending")
    }

    # 1b. KPI Trends (Velocity)
    trend_curr_reports = db.query(Report).filter(Report.timestamp >= trend_curr_start).all()
    curr_kpis = {
        "total": len(trend_curr_reports),
        "verified": sum(1 for r in trend_curr_reports if r.verified),
        "rejected": sum(1 for r in trend_curr_reports if r.status == "Rejected"),
        "pending": sum(1 for r in trend_curr_reports if r.status == "Pending")
    }

    trend_prev_reports = db.query(Report).filter(Report.timestamp >= trend_prev_start, Report.timestamp < trend_curr_start).all()
    prev_kpis = {
        "total": len(trend_prev_reports),
        "verified": sum(1 for r in trend_prev_reports if r.verified),
        "rejected": sum(1 for r in trend_prev_reports if r.status == "Rejected"),
        "pending": sum(1 for r in trend_prev_reports if r.status == "Pending")
    }

    def calc_trend(curr, prev):
        if prev == 0:
            if curr == 0:
                return {"value": "0%", "up": True}
            return {"value": f"+{curr}", "up": True}
        diff = curr - prev
        pct = (diff / prev) * 100
        sign = "+" if diff > 0 else ""
        return {"value": f"{sign}{int(pct)}%", "up": diff >= 0}

    trends = {
        "total": calc_trend(curr_kpis["total"], prev_kpis["total"]),
        "verified": calc_trend(curr_kpis["verified"], prev_kpis["verified"]),
        "rejected": calc_trend(curr_kpis["rejected"], prev_kpis["rejected"]),
        "pending": calc_trend(curr_kpis["pending"], prev_kpis["pending"])
    }

    # 2. Daily Report Volume
    daily_reports = []
    if time_range == "24H":
        # Align to local clock hours
        local_now = now.replace(tzinfo=timezone.utc).astimezone()
        local_now_floored = local_now.replace(minute=0, second=0, microsecond=0)
        base_time = local_now_floored + timedelta(hours=1)
        
        for i in reversed(range(24)):
            local_bucket_start = base_time - timedelta(hours=i+1)
            local_bucket_end = base_time - timedelta(hours=i)
            
            utc_start = local_bucket_start.astimezone(timezone.utc).replace(tzinfo=None)
            utc_end = local_bucket_end.astimezone(timezone.utc).replace(tzinfo=None)
            
            count = sum(1 for r in reports if utc_start <= r.timestamp <= utc_end)
            daily_reports.append({"label": f"{local_bucket_start.hour:02d}:00", "count": count, "showLabel": True})
    else:
        days = 7 if time_range == "7D" else (30 if time_range == "30D" else 365)
        bucket_size = 1 if time_range == "7D" else (7 if time_range == "30D" else 30)
        num_buckets = days // bucket_size
        for i in reversed(range(num_buckets)):
            bucket_start = now - timedelta(days=(i+1)*bucket_size)
            bucket_end = now - timedelta(days=i*bucket_size)
            count = sum(1 for r in reports if bucket_start <= r.timestamp <= bucket_end)
            local_end = bucket_end.replace(tzinfo=timezone.utc).astimezone()
            label = local_end.strftime("%a") if time_range == "7D" else f"W{num_buckets - i}" if time_range == "30D" else local_end.strftime("%b")
            daily_reports.append({"label": label, "count": count, "showLabel": True})

    # 3. Disaster Types
    incidents = db.query(Incident).filter(Incident.created_at >= cutoff).all()
    type_counts = {}
    for inc in incidents:
        dt = inc.disaster_type or "Other"
        if dt not in type_counts:
            type_counts[dt] = {"count": 0, "verified": 0, "pending": 0, "avgResponse": 30, "severity": inc.severity or "Medium"}
        type_counts[dt]["count"] += 1
        if inc.verified:
            type_counts[dt]["verified"] += 1
        if inc.status == "Pending":
            type_counts[dt]["pending"] += 1
            
    disaster_types = [{"label": k, **v} for k, v in type_counts.items()]

    # 4. Community Trust
    reactions = db.query(ReportReaction).join(Incident).filter(Incident.created_at >= cutoff).all()
    upvotes = sum(1 for r in reactions if r.reaction_type.value == "LIKE" or r.reaction_type == "LIKE")
    downvotes = sum(1 for r in reactions if r.reaction_type.value == "DISLIKE" or r.reaction_type == "DISLIKE")
    
    trust = {
        "upvotes": upvotes,
        "downvotes": downvotes,
        "reportCount": len(incidents),
        "avgUpvotes": round(upvotes / len(incidents), 1) if len(incidents) > 0 else 0,
        "avgDownvotes": round(downvotes / len(incidents), 1) if len(incidents) > 0 else 0,
        "topReport": "Recent Verification"
    }

    # 5. Top Reporters
    user_report_counts = {}
    for r in reports:
        if r.user_id:
            uid = str(r.user_id)
            if uid not in user_report_counts:
                user_report_counts[uid] = {"reports": 0, "verified": 0, "rejected": 0, "upvotes": 0}
            user_report_counts[uid]["reports"] += 1
            if r.verified:
                user_report_counts[uid]["verified"] += 1
            if r.status == "Rejected":
                user_report_counts[uid]["rejected"] += 1

    top_reporters_list = []
    for uid, stats in sorted(user_report_counts.items(), key=lambda x: x[1]["reports"], reverse=True)[:5]:
        user = db.query(User).filter(User.id == uid).first()
        if user:
            top_reporters_list.append({
                "name": user.full_name or "Unknown User",
                "location": "Local",
                "reports": stats["reports"],
                "trust": min(100, 50 + stats["verified"] * 10 - stats["rejected"] * 10),
                "verified": stats["verified"],
                "rejected": stats["rejected"],
                "upvotes": stats["upvotes"]
            })

    # 6. Rescue Teams and Response Time (Currently no DB tables for this data)
    rescue_teams = [
        {"initials": "N/A", "name": "Data Not Available", "type": "Unknown", "missions": 0, "successRate": 0, "failed": 0, "avgTime": 0, "controlTime": 0, "status": "Unavailable"}
    ]
    response_time = {
        "dispatch": 0, "onScene": 0, "controlled": 0, "fastest": 0, "slowest": 0
    }

    return {
        "kpis": kpis,
        "trends": trends,
        "dailyReports": daily_reports,
        "disasterTypes": disaster_types,
        "communityTrust": trust,
        "topReporters": top_reporters_list,
        "rescueTeams": rescue_teams,
        "responseTime": response_time
    }


# ======================
# Undo Rescue Assignment
# ======================
@router.delete("/assignments/{assignment_id}")
def undo_rescue_assignment(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    # pyrefly: ignore [missing-import]
    from app.models.incident_assignment import IncidentAssignment
    # pyrefly: ignore [missing-import]
    from app.models.incident import Incident
    
    assignment = db.query(IncidentAssignment).filter(IncidentAssignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
        
    if assignment.status != AssignmentStatus.ASSIGNED:
        raise HTTPException(status_code=400, detail="This assignment can no longer be revoked because the rescue team has already responded.")
        
    incident_id = assignment.incident_id
    
    # Soft delete the assignment via transition service which auto-syncs the incident
    StatusTransitionService.change_assignment_status(db, assignment.id, AssignmentStatus.CANCELLED, current_user.id, "admin", remarks="Cancelled by admin")
    db.commit()

    return {"message": f"Assignment {assignment_id} successfully cancelled"}

# ======================
# Get Status History for an Incident
# ======================
@router.get("/incidents/{incident_id}/history")
def get_incident_history(
    incident_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):
    # pyrefly: ignore [missing-import]
    from app.models.status_history import StatusHistory
    
    # We want history of the Incident itself, and any Assignments belonging to this Incident.
    assignments = db.query(IncidentAssignment).filter(IncidentAssignment.incident_id == incident_id).all()
    assignment_ids = [a.id for a in assignments]

    # Query StatusHistory
    history_records = db.query(StatusHistory).filter(
        ((StatusHistory.entity_type == "Incident") & (StatusHistory.entity_id == incident_id)) |
        ((StatusHistory.entity_type == "Assignment") & (StatusHistory.entity_id.in_(assignment_ids)))
    ).order_by(StatusHistory.timestamp.desc()).all()

    data = []
    for h in history_records:
        role = "System"
        if h.changed_by:
            user = db.query(User).filter(User.id == h.changed_by).first()
            if user:
                role = user.role.value if hasattr(user.role, 'value') else str(user.role)
        
        data.append({
            "id": h.id,
            "entity_type": h.entity_type,
            "entity_id": h.entity_id,
            "old_status": h.old_status,
            "new_status": h.new_status,
            "changed_by": str(h.changed_by) if h.changed_by else None,
            "changed_by_role": role,
            "remarks": h.remarks,
            "created_at": h.timestamp.isoformat() if h.timestamp else None
        })

    return {
        "success": True,
        "data": data
    }


# ======================
# Get Completed Operations (Awaiting Final Review)
# ======================
@router.get("/completed-operations")
def get_completed_operations(
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):
    from app.models.incident_assignment import IncidentAssignment
    from app.core.statuses import AssignmentStatus
    from app.models.rescue_update import RescueUpdate

    # Get all assignments that are active or completed
    all_assignments = db.query(IncidentAssignment).filter(
        IncidentAssignment.status.notin_([AssignmentStatus.CANCELLED, AssignmentStatus.REJECTED])
    ).all()

    # Group assignments by incident ID
    incident_groups = {}
    for a in all_assignments:
        incident_groups.setdefault(a.incident_id, []).append(a)

    # Filter incidents where ALL assignments are COMPLETED
    completed_incident_ids = [
        inc_id for inc_id, group in incident_groups.items()
        if all(a.status == AssignmentStatus.COMPLETED for a in group) and len(group) > 0
    ]

    incidents = db.query(Incident).filter(Incident.id.in_(completed_incident_ids)).all()

    result = []
    for inc in incidents:
        # Fetch individual team reports for this incident
        rescue_updates = db.query(RescueUpdate).filter(RescueUpdate.incident_id == inc.id).all()
        team_reports = []
        for a in incident_groups[inc.id]:
            team_user = db.query(User).filter(User.id == a.team_id).first()
            team_name = team_user.full_name if team_user else "Unknown Team"
            
            # Find report for this team
            update = next((ru for ru in rescue_updates if ru.rescue_team_id == a.team_id and ru.post_incident_report), None)
            
            team_reports.append({
                "team_id": str(a.team_id) if a.team_id else None,
                "team_name": team_name,
                "completed_at": a.completed_at.isoformat() if a.completed_at else None,
                "report": update.post_incident_report if update else None
            })

        result.append({
            "incident_id": str(inc.id),
            "title": inc.title,
            "disaster_type": inc.disaster_type,
            "description": inc.description,
            "severity": inc.severity,
            "location": {
                "address": inc.location,
                "latitude": inc.latitude,
                "longitude": inc.longitude
            },
            "status": inc.status,
            "created_at": inc.created_at.isoformat() if inc.created_at else None,
            "teams": team_reports
        })

    return {
        "success": True,
        "message": "Completed operations fetched successfully",
        "data": result
    }

class FinalReportRequest(BaseModel):
    final_report: str

# ======================
# Submit Final Admin Report (Close Incident)
# ======================
@router.post("/incidents/{incident_id}/final-report")
def submit_final_admin_report(
    incident_id: int,
    payload: FinalReportRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
        
    # Set final report and transition to CLOSED
    incident.final_admin_report = payload.final_report
    
    StatusTransitionService.change_incident_status(
        db, incident.id, IncidentStatus.CLOSED, current_admin.id, "admin", remarks="Final admin review completed"
    )
    
    db.commit()
    db.refresh(incident)

    # Send Notification to reporters
    DISASTER_RADIUS_KM = {"flood": 5.0, "landslide": 3.0, "earthquake": 50.0, "fire": 2.0, "default": 5.0}
    radius_km = DISASTER_RADIUS_KM.get(incident.disaster_type.lower() if incident.disaster_type else "default", 5.0)
    nearby_users = get_users_to_notify(db, incident.latitude, incident.longitude, radius_km) if incident.latitude and incident.longitude else []
    user_ids = [str(u.id) for u in nearby_users]
    if user_ids:
        background_tasks.add_task(
            send_push_notification_task,
            user_ids,
            NotificationType.INCIDENT_CLOSED,
            "Incident Resolved",
            "The disaster has been officially resolved. Stay safe.",
            {"incident_id": str(incident.id)}
        )

    return {
        "message": "Final report submitted and incident closed successfully",
        "incident_id": incident.id
    }

