from pydantic import BaseModel, EmailStr, Field, ConfigDict
from pydantic.alias_generators import to_camel
from typing import List, Optional, Dict
from datetime import date, time


# 공통 설정: 이 설정을 가진 모델은 JSON 변환 시 자동으로 카멜 케이스가 됩니다.
class BaseConfigModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True
    )


# --- 전역 에러 응답 표준 규격---
class ErrorResponse(BaseConfigModel):
    code: str
    message: str
    

# --- 인증 관련 ---
class UserBase(BaseConfigModel):
    nickname: str = Field(..., min_length=2, max_length=10) 
    gender: str
    birth_calendar: str
    birth_date: str
    birth_hour: str
    birth_minute: str
    time_unknown: bool

# 회원가입 요청 (이메일 포함)
class RegisterRequest(UserBase):
    email: EmailStr

# 게스트 요청 (이메일 미포함)
class GuestRegisterRequest(UserBase):
    pass

# 성공 응답 규격
class UserResponse(BaseConfigModel):
    firebase_uid: str
    nickname: str

    class Config:
        from_attributes = True
        
        
# --- 사용자 정보 관련 ---
# 정보 수정 요청
class UserUpdateRequest(BaseConfigModel):
    nickname: Optional[str] = Field(None, min_length=2, max_length=10)
    profile_image_s3_key: Optional[str] = None
    gender: Optional[str] = None
    birth_date: Optional[str] = None
    birth_hour: Optional[str] = None
    birth_minute: Optional[str] = None
    birth_calendar: Optional[str] = None
    time_unknown: Optional[bool] = None

# 정보 조회 응답
class UserInfoResponse(BaseConfigModel):
    email: Optional[str] = None
    nickname: Optional[str] = None
    gender: Optional[str] = None
    birth_date: Optional[date] = None
    birth_time: Optional[time] = None
    birth_calendar: Optional[str] = None
    profile_image: Optional[str] = None
    oheng_wood: Optional[float] = None
    oheng_fire: Optional[float] = None
    oheng_earth: Optional[float] = None
    oheng_metal: Optional[float] = None
    oheng_water: Optional[float] = None

    class Config:
        from_attributes = True
        
class PresignedUrlRequest(BaseConfigModel):
    file_name: str
    content_type: str

class PresignedUrlResponse(BaseConfigModel):
    presigned_url: str
    s3_key: str

# --- 사주 관련 ---
class SajuAnalysisResponse(BaseConfigModel):
    headline: str = Field(..., example="금(金) 기운이 강하고, 목(木) 기운이 부족한 하루예요!")
    advice: str
    oheng_scores: Dict[str, float]
    
    class Config:
        from_attributes = True