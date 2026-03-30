# Import all models so SQLAlchemy's Base.metadata is fully populated
# before create_all() is called in main.py
from app.models.user import User, UserRole
from app.models.attendance import Attendance, WorkMode
from app.models.break_record import BreakRecord, BreakStatus
from app.models.task import Task, TaskStatus, TaskPriority
from app.models.leave import Leave, LeaveType, LeaveStatus
from app.models.notification import Notification, NotificationType

__all__ = [
    "User", "UserRole",
    "Attendance", "WorkMode",
    "BreakRecord", "BreakStatus",
    "Task", "TaskStatus", "TaskPriority",
    "Leave", "LeaveType", "LeaveStatus",
    "Notification", "NotificationType",
]
