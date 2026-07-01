from enum import Enum
from typing import Dict, List, Set

# ==========================================
# 1. ENUMS
# ==========================================

class ReportStatus(str, Enum):
    PENDING = "Pending"
    VERIFIED = "Verified"
    REJECTED = "Rejected"
    MERGED = "Merged"

class IncidentStatus(str, Enum):
    PENDING = "Pending"
    VERIFIED = "Verified"
    ASSIGNED = "Assigned"
    IN_PROGRESS = "In Progress"
    CONTROLLED = "Controlled"
    CLOSED = "Closed"
    REJECTED = "Rejected"

class AssignmentStatus(str, Enum):
    ASSIGNED = "Assigned"
    ACCEPTED = "Accepted"
    REJECTED = "Rejected"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


# ==========================================
# 2. TRANSITION TABLES (State Machine)
# ==========================================

REPORT_TRANSITIONS: Dict[ReportStatus, Set[ReportStatus]] = {
    ReportStatus.PENDING: {ReportStatus.VERIFIED, ReportStatus.REJECTED},
    ReportStatus.VERIFIED: {ReportStatus.MERGED},
    ReportStatus.REJECTED: set(),
    ReportStatus.MERGED: set()
}

INCIDENT_TRANSITIONS: Dict[IncidentStatus, Set[IncidentStatus]] = {
    IncidentStatus.PENDING: {IncidentStatus.VERIFIED, IncidentStatus.REJECTED},
    IncidentStatus.VERIFIED: {IncidentStatus.ASSIGNED, IncidentStatus.PENDING}, # Admin can un-verify to pending
    IncidentStatus.ASSIGNED: {IncidentStatus.IN_PROGRESS, IncidentStatus.VERIFIED}, # Back to Verified if all assignments cancelled/rejected
    IncidentStatus.IN_PROGRESS: {IncidentStatus.CONTROLLED, IncidentStatus.ASSIGNED}, # Back to assigned if team abandons
    IncidentStatus.CONTROLLED: {IncidentStatus.CLOSED, IncidentStatus.IN_PROGRESS}, # Re-open incident if needed
    IncidentStatus.CLOSED: {IncidentStatus.IN_PROGRESS, IncidentStatus.CONTROLLED}, # Re-open
    IncidentStatus.REJECTED: {IncidentStatus.PENDING}, # Accidental rejection recovery
}

ASSIGNMENT_TRANSITIONS: Dict[AssignmentStatus, Set[AssignmentStatus]] = {
    AssignmentStatus.ASSIGNED: {AssignmentStatus.ACCEPTED, AssignmentStatus.REJECTED, AssignmentStatus.CANCELLED},
    AssignmentStatus.ACCEPTED: {AssignmentStatus.IN_PROGRESS, AssignmentStatus.CANCELLED},
    AssignmentStatus.IN_PROGRESS: {AssignmentStatus.COMPLETED, AssignmentStatus.CANCELLED},
    AssignmentStatus.COMPLETED: set(),
    AssignmentStatus.REJECTED: set(),
    AssignmentStatus.CANCELLED: set(),
}


# ==========================================
# 3. PERMISSION MATRIX
# Which roles can trigger which transitions
# Role mapping: 'admin', 'rescue', 'citizen' (citizens can't transition)
# ==========================================

# Format: { (FromStatus, ToStatus): {"admin", "rescue"} }
# If a transition is missing, it assumes no one has permission.

REPORT_PERMISSIONS = {
    (ReportStatus.PENDING, ReportStatus.VERIFIED): {"admin"},
    (ReportStatus.PENDING, ReportStatus.REJECTED): {"admin"},
    (ReportStatus.VERIFIED, ReportStatus.MERGED): {"admin"},
}

INCIDENT_PERMISSIONS = {
    (IncidentStatus.PENDING, IncidentStatus.VERIFIED): {"admin"},
    (IncidentStatus.PENDING, IncidentStatus.REJECTED): {"admin"},
    (IncidentStatus.VERIFIED, IncidentStatus.ASSIGNED): {"admin"}, # Triggered automatically on assignment
    (IncidentStatus.VERIFIED, IncidentStatus.PENDING): {"admin"},
    (IncidentStatus.ASSIGNED, IncidentStatus.VERIFIED): {"admin"}, # Auto-revert if no teams
    (IncidentStatus.ASSIGNED, IncidentStatus.IN_PROGRESS): {"admin", "rescue"}, # Auto-sync when team starts
    (IncidentStatus.IN_PROGRESS, IncidentStatus.CONTROLLED): {"admin"}, # Admin confirms controlled
    (IncidentStatus.IN_PROGRESS, IncidentStatus.ASSIGNED): {"admin"},
    (IncidentStatus.CONTROLLED, IncidentStatus.CLOSED): {"admin"},
    (IncidentStatus.CONTROLLED, IncidentStatus.IN_PROGRESS): {"admin"},
    (IncidentStatus.CLOSED, IncidentStatus.CONTROLLED): {"admin"},
    (IncidentStatus.CLOSED, IncidentStatus.IN_PROGRESS): {"admin"},
    (IncidentStatus.REJECTED, IncidentStatus.PENDING): {"admin"},
}

ASSIGNMENT_PERMISSIONS = {
    (AssignmentStatus.ASSIGNED, AssignmentStatus.ACCEPTED): {"rescue"},
    (AssignmentStatus.ASSIGNED, AssignmentStatus.REJECTED): {"rescue"},
    (AssignmentStatus.ASSIGNED, AssignmentStatus.CANCELLED): {"admin"},
    (AssignmentStatus.ACCEPTED, AssignmentStatus.IN_PROGRESS): {"rescue"},
    (AssignmentStatus.ACCEPTED, AssignmentStatus.CANCELLED): {"admin"},
    (AssignmentStatus.IN_PROGRESS, AssignmentStatus.COMPLETED): {"rescue"},
    (AssignmentStatus.IN_PROGRESS, AssignmentStatus.CANCELLED): {"admin"},
}


# ==========================================
# 4. STATUS METADATA (For API / Logging)
# Matches exactly what UI will render
# ==========================================

def get_status_metadata(status_str: str) -> dict:
    meta_map = {
        # General / Incident / Report
        "Pending": {"color": "Gray", "terminal": False, "priority": 1},
        "Verified": {"color": "Blue", "terminal": False, "priority": 2},
        "Rejected": {"color": "Red", "terminal": True, "priority": 0},
        "Merged": {"color": "Dark Gray", "terminal": True, "priority": 0},
        "Assigned": {"color": "Purple", "terminal": False, "priority": 3},
        "In Progress": {"color": "Orange", "terminal": False, "priority": 4},
        "Controlled": {"color": "Cyan", "terminal": False, "priority": 5},
        "Closed": {"color": "Green", "terminal": True, "priority": 6},
        
        # Assignment Specific
        "Accepted": {"color": "Blue", "terminal": False, "priority": 3},
        "Completed": {"color": "Green", "terminal": True, "priority": 5},
        "Cancelled": {"color": "Dark Gray", "terminal": True, "priority": 0},
    }
    return meta_map.get(status_str, {"color": "Gray", "terminal": False, "priority": 0})
