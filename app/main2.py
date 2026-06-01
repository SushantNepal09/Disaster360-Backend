
# pyrefly: ignore [missing-import]
from fastapi import FastAPI

# pyrefly: ignore [missing-import]
from fastapi.middleware.cors import CORSMiddleware
from app.database import engine, Base
from app.models import User, Incident, Report, ReportMedia, RescueUpdate, RiskZone, ReportEmbedding, ReportReaction, IncidentAssignment
from app.routes import auth, admin, reports, media, rescue, sms_reports

# Create all tables
Base.metadata.create_all(bind=engine)

app2 = FastAPI(title="DISASTER360 API")

# Setup CORS
app2.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app2.get("/")
def home():
    return {"message": "DISASTER360 Backend Running"}

app2.include_router(auth.router)
app2.include_router(admin.router)
app2.include_router(reports.router)
app2.include_router(media.router)
app2.include_router(rescue.router)
app2.include_router(sms_reports.router)
