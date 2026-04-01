"""
Leave Prediction Training Script
=================================
Trains a LogisticRegression model to predict the probability of an employee
taking leave in the next week based on historical leave patterns.

Target model path: leave_model.pkl
"""
import os
import pathlib
import sys
import pandas as pd
import numpy as np
import joblib
from sqlalchemy.orm import Session
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split

# Ensure the app package is discoverable
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent.parent))

from app.core.database import SessionLocal, engine
from app.models.user import User
from app.models.leave import Leave, LeaveStatus

MODEL_PATH = pathlib.Path(__file__).parent / "leave_model.pkl"

def load_and_prepare_data(db: Session) -> pd.DataFrame:
    """Extract and aggregate features from DB into a Pandas DataFrame."""
    # 1. Fetch data
    users_q = db.query(User.id, User.name, User.created_at).filter(User.is_active == 1)
    users_df = pd.read_sql(users_q.statement, engine)
    
    leaves_q = db.query(
        Leave.employee_id, Leave.start_date, Leave.end_date, Leave.total_days, Leave.status
    ).filter(Leave.status == LeaveStatus.approved)
    leaves_df = pd.read_sql(leaves_q.statement, engine)

    # Base dataset is employees
    df = pd.DataFrame({"employee_id": users_df["id"]})
    
    # 2. Build features
    now = pd.Timestamp.now()
    
    # Defaults
    df["leaves_last_30_days"] = 0
    df["leaves_last_90_days"] = 0
    df["total_leaves"] = 0
    df["avg_leave_duration"] = 0.0
    df["recent_leave_gap"] = 999.0  # Sentinel for 'never took leave'
    
    if not leaves_df.empty:
        # Convert dates
        leaves_df["start_date"] = pd.to_datetime(leaves_df["start_date"])
        leaves_df["end_date"] = pd.to_datetime(leaves_df["end_date"])
        
        # Calculate days since each leave
        leaves_df["days_ago"] = (now - leaves_df["end_date"]).dt.days
        
        # Aggregate per employee
        for emp_id in df["employee_id"]:
            emp_leaves = leaves_df[leaves_df["employee_id"] == emp_id]
            if emp_leaves.empty:
                continue
            
            mask_30 = emp_leaves["days_ago"] <= 30
            mask_90 = emp_leaves["days_ago"] <= 90
            
            df.loc[df["employee_id"] == emp_id, "leaves_last_30_days"] = emp_leaves.loc[mask_30, "total_days"].sum()
            df.loc[df["employee_id"] == emp_id, "leaves_last_90_days"] = emp_leaves.loc[mask_90, "total_days"].sum()
            df.loc[df["employee_id"] == emp_id, "total_leaves"] = len(emp_leaves)
            df.loc[df["employee_id"] == emp_id, "avg_leave_duration"] = emp_leaves["total_days"].mean()
            df.loc[df["employee_id"] == emp_id, "recent_leave_gap"] = emp_leaves["days_ago"].min()

    # 3. Create rule-based synthetic target for training
    # "If employee took leave recently -> higher chance else -> lower chance"
    # We combine total_leaves and recency to simulate real patterns
    
    def simulate_target(row):
        prob = 0.1 # Base probability
        
        if row["recent_leave_gap"] < 60:
            prob += 0.4  # Took leave recently
            
        if row["leaves_last_90_days"] > 5:
            prob -= 0.2  # Ran out of leaves? Reduce chance
        elif row["leaves_last_90_days"] > 0:
            prob += 0.2
            
        if row["total_leaves"] > 0 and row["recent_leave_gap"] > 180:
            prob += 0.3  # Hasn't taken leave in a long time
            
        # Add slight randomness
        prob += np.random.uniform(-0.1, 0.1)
        prob = max(0.0, min(1.0, prob))
        
        return 1 if prob > 0.6 else 0

    df["will_take_leave_next_week"] = df.apply(simulate_target, axis=1)
    
    return df


def train():
    print("Connecting to database...")
    db = SessionLocal()
    try:
        df = load_and_prepare_data(db)
        print(f"Extracted {len(df)} employee records.")
        
        if len(df) < 5:
            print("Warning: Very small dataset, generating synthetic employees to ensure model convergence...")
            # Duplicate the DataFrame with noise if we don't have enough users
            synthetic_dfs = []
            for _ in range(20):
                noisy_df = df.copy()
                noisy_df["leaves_last_30_days"] += np.random.randint(0, 3, size=len(df))
                noisy_df["recent_leave_gap"] = np.random.choice([10, 50, 100, 200, 999], size=len(df))
                noisy_df["total_leaves"] += np.random.randint(0, 5, size=len(df))
                # Re-apply target logic to keep it coherent
                noisy_df["will_take_leave_next_week"] = noisy_df.apply(
                    lambda r: 1 if (r["recent_leave_gap"] < 60) or (r["recent_leave_gap"] > 180 and r["total_leaves"] > 0) else 0, 
                    axis=1
                )
                synthetic_dfs.append(noisy_df)
            df = pd.concat([df] + synthetic_dfs, ignore_index=True)
            print(f"Augmented dataset to {len(df)} records.")
            
        
        feature_cols = [
            "leaves_last_30_days", 
            "leaves_last_90_days", 
            "avg_leave_duration", 
            "total_leaves", 
            "recent_leave_gap"
        ]
        
        X = df[feature_cols]
        y = df["will_take_leave_next_week"]
        
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
        
        # Logistic Regression Pipeline
        pipeline = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(class_weight="balanced", random_state=42))
        ])
        
        print("Training LogisticRegression model...")
        pipeline.fit(X_train, y_train)
        
        train_acc = pipeline.score(X_train, y_train)
        test_acc = pipeline.score(X_test, y_test)
        
        print(f"Train Accuracy: {train_acc:.2f}")
        print(f"Test Accuracy:  {test_acc:.2f}")
        
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(pipeline, MODEL_PATH)
        print(f"Model saved to {MODEL_PATH}")
        
    finally:
        db.close()

if __name__ == "__main__":
    train()
