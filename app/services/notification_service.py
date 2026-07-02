import os
import enum
from typing import List, Optional
# pyrefly: ignore [missing-import]
import firebase_admin
from firebase_admin import credentials, messaging
from sqlalchemy.orm import Session
import logging

logger = logging.getLogger(__name__)

# Firebase initialization
try:
    if not firebase_admin._apps:
        # Get credentials from .env
        cred_path = os.getenv("FIREBASE_CREDENTIALS", "firebase-adminsdk.json")
        if os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            logger.info("Firebase Admin initialized successfully.")
        else:
            logger.warning(f"Firebase credentials not found at {cred_path}. Notifications will be mocked/disabled.")
except Exception as e:
    logger.error(f"Error initializing Firebase Admin: {e}")


class NotificationType(str, enum.Enum):
    INCIDENT_CREATED = "incident_created"
    INCIDENT_VERIFIED = "incident_verified"
    EARTHQUAKE_ALERT = "earthquake_alert"
    RESCUE_ASSIGNED = "rescue_assigned"
    RESCUE_UPDATE = "rescue_update"
    INCIDENT_CLOSED = "incident_closed"


def clean_invalid_tokens(invalid_tokens: List[str], db: Session):
    """
    Remove invalid or expired tokens from the database.
    """
    if not invalid_tokens:
        return
    
    from ..models.user import User
    # Find users with these tokens and nullify them
    users = db.query(User).filter(User.fcm_token.in_(invalid_tokens)).all()
    for user in users:
        user.fcm_token = None
    db.commit()


def _send_multicast_sync(tokens: List[str], title: str, body: str, data: dict, db: Session):
    """
    Synchronous internal function to actually send the FCM multicast message.
    """
    if not tokens:
        return
        
    # If firebase is not initialized, just return mock
    if not firebase_admin._apps:
        logger.warning(f"[Mock Notification] To {len(tokens)} tokens: {title} - {body}")
        return

    try:
        # Convert all data values to strings (FCM requirement for data payload)
        stringified_data = {k: str(v) for k, v in data.items() if v is not None}

        # Create message
        message = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data=stringified_data,
            tokens=tokens,
        )
        response = messaging.send_each_for_multicast(message)
        logger.info(f"{response.success_count} messages were sent successfully")
        
        # Handle failures (expired/invalid tokens)
        if response.failure_count > 0:
            invalid_tokens = []
            for i, res in enumerate(response.responses):
                if not res.success:
                    # Common error codes for invalid tokens
                    if res.exception.code in ['messaging/invalid-registration-token', 'messaging/registration-token-not-registered']:
                        invalid_tokens.append(tokens[i])
            
            # Clean up DB
            if invalid_tokens:
                logger.info(f"Cleaning up {len(invalid_tokens)} invalid FCM tokens.")
                clean_invalid_tokens(invalid_tokens, db)
                
    except Exception as e:
        logger.error(f"Failed to send multicast message: {e}")


def send_push_notification_task(
    user_ids: List[str], 
    notification_type: NotificationType, 
    title: str, 
    body: str, 
    data: Optional[dict] = None
):
    """
    This function should be passed to fastapi.BackgroundTasks.
    It fetches tokens for given user_ids, logs the notification, and sends it via FCM.
    Creates its own DB session to avoid closed-session errors.
    """
    from ..models.user import User
    from ..models.notification_log import NotificationLog
    from ..database import SessionLocal
    
    if not user_ids:
        return
        
    db = SessionLocal()
    try:
        data = data or {}
        data['notification_type'] = notification_type.value
        incident_id = data.get('incident_id')
        
        # 1. Fetch valid tokens
        users = db.query(User).filter(User.id.in_(user_ids), User.fcm_token.isnot(None)).all()
        tokens = [u.fcm_token for u in users if u.fcm_token]
        
        if not tokens:
            logger.info("No valid FCM tokens found for the targeted users.")
            return
            
        # 2. Log notification in DB
        try:
            for u in users:
                log_entry = NotificationLog(
                    user_id=u.id,
                    notification_type=notification_type.value,
                    title=title,
                    body=body,
                    incident_id=int(incident_id) if incident_id else None
                )
                db.add(log_entry)
            db.commit()
        except Exception as e:
            logger.error(f"Failed to log notifications: {e}")
            db.rollback()
            
        # 3. Send via FCM in batches of 500 (FCM limit)
        chunk_size = 500
        for i in range(0, len(tokens), chunk_size):
            chunk = tokens[i:i + chunk_size]
            _send_multicast_sync(chunk, title, body, data, db)
    finally:
        db.close()
