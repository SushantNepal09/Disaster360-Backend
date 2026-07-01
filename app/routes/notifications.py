from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from app.database import get_db
from app.models.user import User
from app.models.notification_log import NotificationLog
from app.routes.auth import get_current_user
from sqlalchemy import desc

router = APIRouter(prefix="/notifications", tags=["Notifications"])

@router.get("/")
def get_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Fetch all notifications for the current user, sorted by most recent.
    """
    notifications = (
        db.query(NotificationLog)
        .filter(NotificationLog.user_id == current_user.id)
        .order_by(desc(NotificationLog.sent_at))
        .all()
    )
    
    result = []
    for n in notifications:
        result.append({
            "id": n.id,
            "type": n.notification_type,
            "title": n.title,
            "message": n.body,
            "time": n.sent_at.isoformat() + "Z",
            "is_read": n.is_read
        })
        
    return result

@router.patch("/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Mark a specific notification as read.
    """
    notification = db.query(NotificationLog).filter(
        NotificationLog.id == notification_id,
        NotificationLog.user_id == current_user.id
    ).first()
    
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
        
    notification.is_read = True
    db.commit()
    
    return {"status": "success", "message": "Notification marked as read"}

@router.patch("/action/mark-all-read")
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Mark all notifications for the current user as read.
    """
    db.query(NotificationLog).filter(
        NotificationLog.user_id == current_user.id,
        NotificationLog.is_read == False
    ).update({"is_read": True})
    
    db.commit()
    
    return {"status": "success", "message": "All notifications marked as read"}
