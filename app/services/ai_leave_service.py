"""
AI Leave Service
=================
Service layer containing business logic to process employee data and 
provide leave probability predictions, classifying team shortage risks.
"""
from __future__ import annotations
import datetime
from sqlalchemy.orm import Session
from app.models.user import User
from app.models.leave import Leave, LeaveStatus

def get_leave_predictions(db: Session, request_user: dict | None = None) -> dict:
    """
    Returns a dictionary of:
    - predictions: list of employee prediction objects
    - team_alert: str | None (if surge risk)
    """
    # 1. Fetch active employees (could scope this down if needed)
    employees = db.query(User).filter(User.is_active == 1).all()
    
    # 2. Fetch all approved leaves
    approved_leaves = db.query(Leave).filter(Leave.status == LeaveStatus.approved).all()
    
    # Group leaves by employee for easy feature building
    from collections import defaultdict
    leaves_by_employee = defaultdict(list)
    for leave in approved_leaves:
        leaves_by_employee[leave.employee_id].append(leave)
        
    now = datetime.date.today()
    predictions_list = []
    high_risk_count = 0
    
    from app.ml.leave_prediction.predict import predict_leave_probability
    
    # 3. Build features and evaluate probability
    for emp in employees:
        emp_leaves = leaves_by_employee.get(emp.id, [])
        
        # Calculate features
        leaves_last_30 = 0
        leaves_last_90 = 0
        total_leaves = len(emp_leaves)
        sum_durations = 0.0
        gap = 999.0
        
        for L in emp_leaves:
            sum_durations += L.total_days
            delta = (now - L.end_date).days
            
            # Recency
            if delta < gap:
                gap = delta
                
            if delta <= 30:
                leaves_last_30 += L.total_days
            if delta <= 90:
                leaves_last_90 += L.total_days
                
        avg_dur = (sum_durations / total_leaves) if total_leaves > 0 else 0.0
        
        features = {
            "leaves_last_30_days": leaves_last_30,
            "leaves_last_90_days": leaves_last_90,
            "avg_leave_duration": avg_dur,
            "total_leaves": total_leaves,
            "recent_leave_gap": float(gap),
        }
        
        # 4. Invoke Prediction (fallback safely wrapped)
        try:
            prob = predict_leave_probability(features)
        except Exception:
            prob = 0.1 # Absolute fail-safe
            
        # 5. Classify Risk
        if prob >= 0.7:
            risk = "🔴 High"
            high_risk_count += 1
        elif prob >= 0.4:
            risk = "🟡 Medium"
        else:
            risk = "🟢 Low"
            
        predictions_list.append({
            "employee_id": emp.id,
            "employee": emp.name,
            "probability": prob,
            "risk": risk
        })
        
    # Sort from highest to lowest probability
    predictions_list.sort(key=lambda x: x["probability"], reverse=True)
    
    # 6. Check for Team-level Alerts
    alert_msg = None
    if high_risk_count >= 3:
        alert_msg = f"⚠️ {high_risk_count} employees likely to take leave next week"
        
    return {
        "predictions": predictions_list,
        "team_alert": alert_msg
    }
