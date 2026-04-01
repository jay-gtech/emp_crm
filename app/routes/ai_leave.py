"""
AI Leave Prediction API Route
=============================
REST Endpoint separating logic from the service layer.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.core.auth import login_required
from app.core.database import get_db

router = APIRouter(prefix="/ai", tags=["ai", "leave"])

@router.get("/leave-predictions")
def leave_predictions(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required)
):
    """
    Returns AI-enriched leave predictions, highlighting shortage risks.
    Only allows access to users who are authenticated.
    """
    try:
        from app.services.ai_leave_service import get_leave_predictions
        result = get_leave_predictions(db, current_user)
        return JSONResponse(content=result)
    except Exception as exc:
        return JSONResponse(
            content={"error": str(exc), "predictions": [], "team_alert": None},
            status_code=500
        )
