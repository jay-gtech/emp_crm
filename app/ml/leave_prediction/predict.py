"""
Leave Prediction Module
=======================
Exposes the online inference engine for predicting employee leave probability.
Includes a safe fallback to a rule-based system if the model is missing.
"""
import pathlib
import json

MODEL_PATH = pathlib.Path(__file__).parent / "leave_model.pkl"

_model = None
_model_loaded = False

def _load_model():
    global _model, _model_loaded
    if _model_loaded:
        return _model
        
    _model_loaded = True
    if not MODEL_PATH.exists():
        return None
        
    try:
        import joblib
        _model = joblib.load(MODEL_PATH)
    except Exception:
        _model = None
        
    return _model

def _rule_based_probability(features: dict) -> float:
    """
    Fallback prediction logic if the ML model is unavailable.
    features: {
        'leaves_last_30_days': int,
        'leaves_last_90_days': int,
        'avg_leave_duration': float,
        'total_leaves': int,
        'recent_leave_gap': float
    }
    """
    gap = features.get("recent_leave_gap", 999.0)
    recent_90 = features.get("leaves_last_90_days", 0)
    total = features.get("total_leaves", 0)
    
    prob = 0.1
    
    if gap < 60:
        prob += 0.4
    if recent_90 > 5:
        prob -= 0.2
    elif recent_90 > 0:
        prob += 0.2
        
    if total > 0 and gap > 180:
        prob += 0.3
        
    return max(0.0, min(1.0, prob))

def predict_leave_probability(features: dict) -> float:
    """
    Predict probability (0-1) of employee taking leave in the next week.
    Safely falls back to rules if ML loading fails.
    """
    model = _load_model()
    
    if model is None:
        return float(round(_rule_based_probability(features), 2))
        
    try:
        import pandas as pd
        # Ensure correct column order
        cols = [
            "leaves_last_30_days", 
            "leaves_last_90_days", 
            "avg_leave_duration", 
            "total_leaves", 
            "recent_leave_gap"
        ]
        
        # Build 1-row dataframe
        row = {c: features.get(c, 0.0) for c in cols}
        if "recent_leave_gap" not in features:
            row["recent_leave_gap"] = 999.0
            
        df = pd.DataFrame([row])
        
        # predict_proba returns [[prob_0, prob_1]]
        probs = model.predict_proba(df)[0]
        # ensure we have two classes
        if len(probs) > 1:
            return float(round(probs[1], 2))
        else:
            # edge case if only one class was present during training
            return 0.0 if model.classes_[0] == 0 else 1.0
            
    except Exception as exc:
        # Fallback to rules instead of crashing the API
        return float(round(_rule_based_probability(features), 2))
