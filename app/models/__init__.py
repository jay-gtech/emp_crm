# Import all models so SQLAlchemy's Base.metadata is fully populated
# before create_all() is called in main.py
from app.models.user import User, UserRole
from app.models.attendance import Attendance, WorkMode
from app.models.break_record import BreakRecord, BreakStatus
from app.models.task import Task, TaskStatus, TaskPriority
from app.models.leave import Leave, LeaveType, LeaveStatus
from app.models.notification import Notification
from app.models.audit_log import AuditLog, AuditAction
from app.models.announcement import Announcement
from app.models.meeting import Meeting
from app.models.message import Message
from app.models.location_log import LocationLog
from app.models.task_comment import TaskComment
from app.models.visitor import Visitor
from app.models.report import Report
from app.models.eod_report import EODReport
from app.models.expense import ExpenseGroup, ExpenseMember

__all__ = [
    "User", "UserRole",
    "Attendance", "WorkMode",
    "BreakRecord", "BreakStatus",
    "Task", "TaskStatus", "TaskPriority",
    "Leave", "LeaveType", "LeaveStatus",
    "Notification",
    "AuditLog", "AuditAction",
    "Announcement",
    "Meeting",
    "Message",
    "LocationLog",
    "TaskComment",
    "Visitor",
    "Report",
    "EODReport",
    "ExpenseGroup",
    "ExpenseMember",
]
