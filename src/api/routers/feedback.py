from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.schemas import FeedbackCreateRequest, FeedbackResponse, UserStyleProfileResponse
from src.db.session import get_db
from src.services.feedback_service import FeedbackService

router = APIRouter(prefix="/feedback", tags=["feedback-learning"])


@router.post("", response_model=FeedbackResponse)
async def create_feedback(
    payload: FeedbackCreateRequest,
    session: AsyncSession = Depends(get_db),
):
    service = FeedbackService(session)
    return await service.create_feedback(payload)


@router.get("/style/{user_id}", response_model=UserStyleProfileResponse)
async def get_style_profile(
    user_id: str,
    session: AsyncSession = Depends(get_db),
):
    service = FeedbackService(session)
    profile = await service.get_user_style_profile(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Style profile not found")
    return profile
