
from fastapi import APIRouter, Depends, HTTPException, status

from sqlalchemy.orm import Session

from pydantic import BaseModel
import json

from app.database import get_db
from app.models import SmsReport
from app.auth.api_key import verify_sms_api_key
from app.routes.reports import process_disaster_report, ReportCreateRequest

router = APIRouter(prefix="/sms-reports", tags=["SMS Gateway"])

class SMSReportRequest(BaseModel):
    title: str
    description: str
    severity: str
    latitude: float
    longitude: float
    sender_number: str
    raw_message: str
    disaster_type: str = "default"
    location: str | None = None
    user_id: str | None = None
    uuid: str | None = None
    created_at: str | None = None
    gateway_metadata: dict | None = None

@router.post("/")
def receive_sms_report(
    payload: SMSReportRequest,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_sms_api_key)
):
    # Support both uuid and user_id for flexibility
    final_user_id = payload.uuid or payload.user_id

    # Prepare standard report payload for matching/merging
    core_payload = ReportCreateRequest(
        title=payload.title,
        description=payload.description,
        disaster_type=payload.disaster_type,
        severity=payload.severity,
        latitude=payload.latitude,
        longitude=payload.longitude,
        location=payload.location,
        created_at=payload.created_at
    )
    
    # Process the core report (match or create new incident)
    new_report, response_data = process_disaster_report(core_payload, db, user_id=final_user_id)
    
    # Create the SMS-specific record linked to the new report
    metadata_str = json.dumps(payload.gateway_metadata) if payload.gateway_metadata else None
    sms_entry = SmsReport(
        report_id=new_report.id,
        user_id=final_user_id,
        sender_number=payload.sender_number,
        raw_message=payload.raw_message,
        metadata_json=metadata_str
    )
    
    db.add(sms_entry)
    db.commit()
    db.refresh(sms_entry)
    
    # Return augmented response to the gateway
    response_data["message"] = "SMS " + response_data["message"]
    response_data["sms_report_id"] = sms_entry.id
    
    return response_data
