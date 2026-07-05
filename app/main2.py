
# pyrefly: ignore [missing-import]
from fastapi import FastAPI

# pyrefly: ignore [missing-import]
from fastapi.middleware.cors import CORSMiddleware
from app.database import engine, Base
from app.models import User, Incident, Report, ReportMedia, RescueUpdate, RiskZone, ReportEmbedding, ReportReaction, IncidentAssignment, NotificationLog
from app.routes import auth, admin, reports, media, rescue, sms_reports, notifications

# Create all tables moved to start.sh background script

app2 = FastAPI(title="DISASTER360 API")

# Setup CORS
app2.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app2.on_event("startup")
def sync_closed_incidents():
    from app.database import SessionLocal
    from app.core.statuses import IncidentStatus
    db = SessionLocal()
    try:
        completed_assignments = db.query(IncidentAssignment).all()
        for a in completed_assignments:
            if str(a.status).lower() in ['completed', 'controlled', 'closed', 'resolved']:
                inc = db.query(Incident).filter(Incident.id == a.incident_id).first()
                if inc and inc.status != IncidentStatus.CLOSED:
                    inc.status = IncidentStatus.CLOSED
                    inc.verified = True
                    for r in inc.reports:
                        r.status = IncidentStatus.CLOSED
                        r.verified = True
        db.commit()
    except Exception as e:
        print("Startup sync error:", e)
    finally:
        db.close()

@app2.get("/")
def home():
    return {"message": "DISASTER360 Backend Running"}

app2.include_router(auth.router)
app2.include_router(admin.router)
app2.include_router(reports.router)
app2.include_router(media.router)
app2.include_router(rescue.router)
app2.include_router(sms_reports.router)
app2.include_router(notifications.router)
