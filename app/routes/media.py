
from fastapi import APIRouter, Depends, HTTPException

from sqlalchemy.orm import Session

from pydantic import BaseModel
from typing import List

from ..database import get_db
from ..models.report import Report
from ..models.report_media import ReportMedia
from ..models.user import User
from .auth import get_current_user  

# Changed prefix to mount under /reports
router = APIRouter(prefix="/reports", tags=["Media"])

class MediaUrlRequest(BaseModel):
    media_urls: List[str]
    file_type: str = "image"

# ======================
# Attach Media URLs to Report (citizen or approved admin)
# ======================
@router.post("/{report_id}/media")
def attach_media(
    report_id: int,
    payload: MediaUrlRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Validate payload
    if not payload.media_urls:
        raise HTTPException(status_code=400, detail="No media URLs provided")
    if len(payload.media_urls) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 images allowed")
    
    # Optional: Basic validation to ensure URLs belong to Supabase (adapt to your actual domain)
    for url in payload.media_urls:
        if "supabase.co" not in url:
            raise HTTPException(status_code=400, detail=f"Invalid media URL: {url}. Only Supabase URLs are allowed.")

    from ..models.incident import Incident

    # Check incident exists
    incident = db.query(Incident).filter(Incident.id == report_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    # We allow any user who has submitted a report to this incident, or any admin, to attach media
    # Let's just check if the user has a report in this incident
    user_has_reported = any(r.user_id == current_user.id for r in incident.reports)
    if not user_has_reported and current_user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Not authorized to add media for this incident"
        )

    # Insert into DB
    saved_media = []
    for url in payload.media_urls:
        # Check duplicate
        existing_media = db.query(ReportMedia).filter(
            ReportMedia.incident_id == report_id,
            ReportMedia.report_id.is_(None),
            ReportMedia.file_path == url
        ).first()

        if existing_media:
            continue # Skip duplicates
        
        media = ReportMedia(
            incident_id=report_id,
            report_id=None,
            user_id=current_user.id,
            file_path=url,
            file_type=payload.file_type
        )
        db.add(media)
        saved_media.append(url)

    db.commit()

    return {
        "message": f"Successfully attached {len(saved_media)} media URLs",
        "saved_urls": saved_media
    }

@router.post("/submissions/{sub_id}/media")
def attach_submission_media(
    sub_id: int,
    payload: MediaUrlRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not payload.media_urls:
        raise HTTPException(status_code=400, detail="No media URLs provided")
    if len(payload.media_urls) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 images allowed")
    
    for url in payload.media_urls:
        if "supabase.co" not in url:
            raise HTTPException(status_code=400, detail=f"Invalid media URL: {url}. Only Supabase URLs are allowed.")

    sub = db.query(Report).filter(Report.id == sub_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")

    if sub.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Not authorized to add media for this submission"
        )

    saved_media = []
    for url in payload.media_urls:
        existing_media = db.query(ReportMedia).filter(
            ReportMedia.incident_id == sub.incident_id,
            ReportMedia.report_id == sub_id,
            ReportMedia.file_path == url
        ).first()

        if existing_media:
            continue 
        
        media = ReportMedia(
            incident_id=sub.incident_id,
            report_id=sub_id,
            user_id=current_user.id,
            file_path=url,
            file_type=payload.file_type
        )
        db.add(media)
        saved_media.append(url)

    db.commit()

    return {
        "message": f"Successfully attached {len(saved_media)} media URLs to submission",
        "saved_urls": saved_media
    }
