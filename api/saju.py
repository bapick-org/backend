from fastapi import APIRouter, Depends, Depends
from sqlalchemy.orm import Session
from typing import Dict, Tuple, List

from core.firebase_auth import verify_firebase_token
from core.db import get_db
from core.models import User
from core.schemas import SajuAnalysisResponse
from core.exceptions import NotFoundException, InternalServerErrorException
from saju.saju_service import get_today_saju_analysis

router = APIRouter(prefix="/saju", tags=["saju"])

# 사용자의 사주 오행 분석 결과 반환
@router.get("", response_model=SajuAnalysisResponse)
async def get_personalized_recommendation(
    uid: str = Depends(verify_firebase_token),
    db: Session = Depends(get_db)
):
    result = await get_today_saju_analysis(uid, db)    
    
    return SajuAnalysisResponse(
        headline=result["headline"],
        advice=result["advice"],
        oheng_scores=result["oheng_scores"]
    )