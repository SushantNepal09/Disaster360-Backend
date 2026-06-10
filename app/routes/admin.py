# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session

# pyrefly: ignore [missing-import]
from pydantic import BaseModel
from typing import Optional, List

from ..database import get_db
from ..models.incident import Incident
from ..models.incident_assignment import IncidentAssignment
from ..models.report import Report
from ..models.report_reaction import ReportReaction
from ..models.user import User
from .auth import get_current_admin
from datetime import datetime, timedelta, timezone

router = APIRouter(prefix="/admin", tags=["Admin"])


# ======================
# Pydantic Schemas
# ======================
class AssignTeamRequest(BaseModel):
    team_names: List[str]


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
    from .reports import serialize_incident
    
    incidents = (
        db.query(Incident)
        .options(
            joinedload(Incident.reports).joinedload(Report.user),
            joinedload(Incident.reactions)
        )
        .all()
    )
    
    result = [serialize_incident(inc, current_user.id) for inc in incidents]
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    # report_id here is actually the incident id sent from the frontend
    incident = db.query(Incident).filter(Incident.id == report_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Report not found")

    # Mark the incident as verified
    incident.status = "Verified"
    incident.verified = True

    # Mark every linked report as verified
    for r in incident.reports:
        r.status = "Verified"
        r.verified = True

    db.commit()
    db.refresh(incident)

    return {
        "message": f"Incident {report_id} verified successfully",
        "verified_by": current_user.email
    }


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

    incident.status = "Pending"
    incident.verified = False

    for r in incident.reports:
        r.status = "Pending"
        r.verified = False

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

    if payload.status not in allowed_status:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Allowed values: {allowed_status}"
        )

    incident = db.query(Incident).filter(Incident.id == report_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    incident.status = payload.status

    # Cascade the status to all linked reports
    for r in incident.reports:
        r.status = payload.status

    db.commit()
    db.refresh(incident)

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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    incident = db.query(Incident).filter(Incident.id == report_id).first()

    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    if incident.status == "Pending":
        raise HTTPException(status_code=400, detail="Pending reports cannot be assigned to rescue teams")

    # Clear existing assignments
    db.query(IncidentAssignment).filter(IncidentAssignment.incident_id == incident.id).delete()

    from ..models.rescue_update import RescueUpdate

    # Drop old RescueUpdate records for teams that are no longer assigned
    if not payload.team_names:
        db.query(RescueUpdate).filter(RescueUpdate.incident_id == incident.id).delete()
    else:
        assigned_users = db.query(User).filter(User.full_name.in_(payload.team_names)).all()
        assigned_user_ids = [u.id for u in assigned_users]
        if assigned_user_ids:
            db.query(RescueUpdate).filter(
                RescueUpdate.incident_id == incident.id,
                ~RescueUpdate.rescue_team_id.in_(assigned_user_ids)
            ).delete()
        else:
            db.query(RescueUpdate).filter(RescueUpdate.incident_id == incident.id).delete()

    # Add new ones
    for t in payload.team_names:
        db.add(IncidentAssignment(incident_id=incident.id, team_name=t))

    if payload.team_names:
        incident.status = "Assigned"
        for r in incident.reports:
            r.status = "Assigned"
    elif incident.status == "Assigned":
        incident.status = "Verified"
        for r in incident.reports:
            r.status = "Verified"

    db.commit()
    db.refresh(incident)

    return {
        "message": "Teams assigned successfully",
        "report_id": incident.id,
        "assigned_teams": payload.team_names,
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

    incident.status = "Rejected"
    
    # Cascade rejection to linked reports
    for r in incident.reports:
        r.status = "Rejected"

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

    incident.status = "Pending"
    
    # Cascade undo rejection to linked reports
    for r in incident.reports:
        r.status = "Pending"

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
    from ..models.rescue_update import RescueUpdate
    operations = db.query(RescueUpdate).filter(RescueUpdate.status != "Closed").all()
    
    result = []
    for op in operations:
        incident = db.query(Incident).filter(Incident.id == op.incident_id).first()
        team_user = db.query(User).filter(User.id == op.rescue_team_id).first()
        
        result.append({
            "initials": "".join([part[0] for part in team_user.full_name.split()[:2]]).upper() if team_user and team_user.full_name else "RT",
            "name": team_user.full_name if team_user else "Unknown Team",
            "locationStatus": f"{incident.location if incident and incident.location else 'Unknown'} — {op.status}",
            "badge": "Active" if op.is_acknowledged else "Dispatch",
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
            reports_info.append({
                "id": f"RPT-{r.id:05d}",
                "title": f"{inc.disaster_type} — {inc.location if inc.location else 'Unknown'}",
                "date": r.timestamp.strftime("%b %d, %Y") if getattr(r, "timestamp", None) else inc.created_at.strftime("%b %d, %Y"),
                "reporter": user.full_name if user else "Unknown"
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

