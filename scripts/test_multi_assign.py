"""Final smoke test for multi-assign feature."""
import sys
sys.path.insert(0, '.')

# 1. Imports
from app.core.constants import MAX_BATCH_ASSIGN
from app.models.task import Task
from app.services.task_service import create_task, create_tasks_bulk, get_batch_tasks
from app.routes.tasks import router
print("1. All imports OK")
print(f"   MAX_BATCH_ASSIGN = {MAX_BATCH_ASSIGN}")

# 2. Task model column
assert hasattr(Task, 'batch_id'), "batch_id missing from Task model"
print("2. Task.batch_id column defined OK")

# 3. Router routes
route_paths = [r.path for r in router.routes]
print("   Available routes:", route_paths)
required = ["/tasks/create", "/tasks/{task_id}/start", "/tasks/{task_id}/submit",
            "/tasks/{task_id}/approve", "/tasks/{task_id}/reject", "/tasks/{task_id}/delete"]
for p in required:
    assert p in route_paths, f"Missing route: {p}"
print("3. All existing routes present")

# 4. create_tasks_bulk signature
import inspect
sig = inspect.signature(create_tasks_bulk)
params = list(sig.parameters.keys())
assert 'assigned_to_ids' in params
print("4. create_tasks_bulk signature OK:", params)

# 5. DB column confirmation
from app.core.database import engine
from sqlalchemy import text
with engine.connect() as conn:
    rows = conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='tasks' AND column_name='batch_id'"
    )).fetchall()
    assert len(rows) > 0, "batch_id NOT in DB!"
    print("5. DB column batch_id confirmed in PostgreSQL tasks table")

print()
print("=== ALL CHECKS PASSED ===")
