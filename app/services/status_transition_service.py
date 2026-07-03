from sqlalchemy.orm import Session
# pyrefly: ignore [missing-import]
from fastapi import HTTPException
from app.models.incident import Incident
from app.models.report import Report
from app.models.incident_assignment import IncidentAssignment
from app.models.status_history import StatusHistory
from app.core.statuses import (
    IncidentStatus, ReportStatus, AssignmentStatus,
    INCIDENT_TRANSITIONS, REPORT_TRANSITIONS, ASSIGNMENT_TRANSITIONS,
    INCIDENT_PERMISSIONS, REPORT_PERMISSIONS, ASSIGNMENT_PERMISSIONS
)

class StatusTransitionService:

    @staticmethod
    def _validate_transition(entity_type: str, old_status: str, new_status: str, user_role: str):
        if old_status == new_status:
            return  # Idempotent

        # Choose the right maps based on entity
        if entity_type == 'Incident':
            transitions = INCIDENT_TRANSITIONS
            permissions = INCIDENT_PERMISSIONS
        elif entity_type == 'Report':
            transitions = REPORT_TRANSITIONS
            permissions = REPORT_PERMISSIONS
        elif entity_type == 'Assignment':
            transitions = ASSIGNMENT_TRANSITIONS
            permissions = ASSIGNMENT_PERMISSIONS
        else:
            raise ValueError(f"Unknown entity type: {entity_type}")

        # Check Transition
        allowed_next_states = transitions.get(old_status, set())
        if new_status not in allowed_next_states:
            raise HTTPException(status_code=400, detail=f"Invalid transition from {old_status} to {new_status} for {entity_type}.")

        # Check Permissions
        allowed_roles = permissions.get((old_status, new_status), set())
        if user_role not in allowed_roles:
            raise HTTPException(status_code=403, detail=f"Role '{user_role}' is not allowed to transition {entity_type} to {new_status}.")

    @staticmethod
    def _record_history(db: Session, entity_type: str, entity_id: int, old_status: str, new_status: str, user_id, remarks: str = None):
        if old_status == new_status:
            return

        history = StatusHistory(
            entity_type=entity_type,
            entity_id=entity_id,
            old_status=old_status,
            new_status=new_status,
            changed_by=user_id,
            remarks=remarks
        )
        db.add(history)

    @staticmethod
    def change_incident_status(db: Session, incident_id: int, new_status: str, user_id, user_role: str, remarks: str = None):
        incident = db.query(Incident).filter(Incident.id == incident_id).first()
        if not incident:
            raise HTTPException(status_code=404, detail="Incident not found")

        old_status = incident.status
        if old_status == new_status:
            return incident
            
        StatusTransitionService._validate_transition('Incident', old_status, new_status, user_role)
        
        incident.status = new_status
        
        # Admin Verified logic (mark as verified bool too)
        if new_status in [IncidentStatus.VERIFIED, IncidentStatus.ASSIGNED, IncidentStatus.IN_PROGRESS, IncidentStatus.CONTROLLED, IncidentStatus.CLOSED]:
            incident.verified = True
        elif new_status == IncidentStatus.PENDING:
            incident.verified = False

        # Propagate status to ALL child reports (Rule A & D)
        reports = db.query(Report).filter(Report.incident_id == incident.id).all()
        for r in reports:
            if r.status != new_status:
                old_r_status = r.status
                r.status = new_status
                r.verified = incident.verified
                StatusTransitionService._record_history(
                    db, 'Report', r.id, old_r_status, new_status, user_id, 
                    remarks=f"Inherited status from parent incident update: {remarks or ''}".strip()
                )

        StatusTransitionService._record_history(db, 'Incident', incident_id, old_status, new_status, user_id, remarks)
        return incident

    @staticmethod
    def change_report_status(db: Session, report_id: int, new_status: str, user_id, user_role: str, remarks: str = None):
        report = db.query(Report).filter(Report.id == report_id).first()
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")

        old_status = report.status
        if old_status == new_status:
            return report
            
        StatusTransitionService._validate_transition('Report', old_status, new_status, user_role)
        
        report.status = new_status
        report.verified = (new_status == ReportStatus.VERIFIED)
        StatusTransitionService._record_history(db, 'Report', report_id, old_status, new_status, user_id, remarks)
        return report

    @staticmethod
    def change_assignment_status(db: Session, assignment_id: int, new_status: str, user_id, user_role: str, remarks: str = None):
        assignment = db.query(IncidentAssignment).filter(IncidentAssignment.id == assignment_id).first()
        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found")

        old_status = assignment.status
        if old_status == new_status:
            return assignment
            
        StatusTransitionService._validate_transition('Assignment', old_status, new_status, user_role)
        
        assignment.status = new_status
        StatusTransitionService._record_history(db, 'Assignment', assignment_id, old_status, new_status, user_id, remarks)
        
        # After saving assignment, flush and check if parent incident needs auto-updating
        db.flush()
        StatusTransitionService._update_derived_statuses(db, assignment.incident_id, user_id, user_role)
        return assignment

    @staticmethod
    def _update_derived_statuses(db: Session, incident_id: int, user_id, user_role: str):
        # Synchronize incident status based on its assignments
        incident = db.query(Incident).filter(Incident.id == incident_id).first()
        if not incident:
            return
            
        # Only auto-sync if incident is verified or beyond (skip pending, rejected)
        if incident.status in [IncidentStatus.PENDING, IncidentStatus.REJECTED]:
            return
            
        assignments = db.query(IncidentAssignment).filter(IncidentAssignment.incident_id == incident_id).all()
        
        # Filter out cancelled/rejected ones for derivation logic
        active_assignments = [a for a in assignments if a.status not in [AssignmentStatus.CANCELLED, AssignmentStatus.REJECTED]]
        
        new_derived_status = None
        
        if not active_assignments:
            # Revert to verified if all teams rejected or cancelled
            new_derived_status = IncidentStatus.VERIFIED
        else:
            # Prioritize states: In Progress > Assigned
            # If ANY team is in progress or accepted
            if any(a.status in [AssignmentStatus.IN_PROGRESS, AssignmentStatus.ACCEPTED] for a in active_assignments):
                new_derived_status = IncidentStatus.IN_PROGRESS
            # Else if ANY team is assigned (and none accepted/in progress)
            elif any(a.status == AssignmentStatus.ASSIGNED for a in active_assignments):
                new_derived_status = IncidentStatus.IN_PROGRESS if incident.status == IncidentStatus.IN_PROGRESS else IncidentStatus.ASSIGNED
            else:
                # All active teams are Completed.
                # Do NOT auto-transition to Controlled. Keep it whatever it was (In Progress), 
                # Admin will manually mark it as Controlled / Closed.
                pass
                
        if new_derived_status and incident.status != new_derived_status:
            # Allow the service to self-transition as an 'admin' action if needed, or bypass validation
            # Since this is an auto-sync, we bypass validation for internal state updates
            old_status = incident.status
            incident.status = new_derived_status
            
            if new_derived_status in [IncidentStatus.VERIFIED, IncidentStatus.ASSIGNED, IncidentStatus.IN_PROGRESS, IncidentStatus.CONTROLLED, IncidentStatus.CLOSED]:
                incident.verified = True
            elif new_derived_status == IncidentStatus.PENDING:
                incident.verified = False
                
            # Propagate to children
            reports = db.query(Report).filter(Report.incident_id == incident.id).all()
            for r in reports:
                if r.status != new_derived_status:
                    old_r_status = r.status
                    r.status = new_derived_status
                    r.verified = incident.verified
                    StatusTransitionService._record_history(
                        db, 'Report', r.id, old_r_status, new_derived_status, user_id, 
                        remarks="Auto-synced from assignment changes"
                    )
            
            StatusTransitionService._record_history(
                db, 'Incident', incident.id, old_status, new_derived_status, user_id, 
                remarks="Auto-synced from assignment changes"
            )
