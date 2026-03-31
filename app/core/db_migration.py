import logging
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

def apply_safe_migrations(engine: Engine):
    """
    Checks for missing columns in the 'users' table and adds them safely.
    Ensures the application does not crash on DB schema mismatch.
    """
    try:
        with engine.begin() as conn:
            # Check existing columns using SQLite PRAGMA
            result = conn.execute(text("PRAGMA table_info(users);")).fetchall()
            existing_columns = [row[1] for row in result]
            
            # 1. manager_id
            if "manager_id" not in existing_columns:
                try:
                    conn.execute(text("ALTER TABLE users ADD COLUMN manager_id INTEGER;"))
                    print("manager_id column added")
                except Exception as e:
                    print(f"Failed to add manager_id: {e}")
            else:
                print("manager_id already exists")
                
            # 2. team_lead_id
            if "team_lead_id" not in existing_columns:
                try:
                    conn.execute(text("ALTER TABLE users ADD COLUMN team_lead_id INTEGER;"))
                    print("team_lead_id column added")
                except Exception as e:
                    print(f"Failed to add team_lead_id: {e}")
            else:
                print("team_lead_id already exists")

            # 3. audit_log_id
            notif_info = conn.execute(text("PRAGMA table_info(notifications);")).fetchall()
            notif_columns = [row[1] for row in notif_info]
            if "audit_log_id" not in notif_columns:
                try:
                    conn.execute(text("ALTER TABLE notifications ADD COLUMN audit_log_id INTEGER REFERENCES audit_logs(id);"))
                    print("audit_log_id column added")
                except Exception as e:
                    print(f"Failed to add audit_log_id: {e}")
            else:
                print("audit_log_id already exists")

    except Exception as e:
        print(f"Migration skipped or failed: {e}")
