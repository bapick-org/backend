from pydantic import BaseModel, EmailStr, Field, ConfigDict
from pydantic.alias_generators import to_camel
from typing import List, Optional, Dict
from datetime import date, datetime, time


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
        
# --- 스크랩 관련 ---
class ScrapCreate(BaseConfigModel):
    restaurant_id: int
    collection_id: int | None = None

class CollectionCreate(BaseConfigModel):
    name: str

class CollectionResponse(BaseConfigModel):
    id: int
    name: str
    image_url: Optional[str] = ""
    created_at: datetime
    has_scraps: bool
    is_system_default: bool = False  # 모든 스크랩 카드

    @classmethod
    def from_orm_custom(cls, collection, latest_scrap):
        """DB 객체를 응답용 스키마로 변환하는 헬퍼 메서드"""
        image_url = ""
        if latest_scrap and latest_scrap.restaurant and latest_scrap.restaurant.image:
            image_field = latest_scrap.restaurant.image
            images = [url.strip() for url in image_field.split(',') if url.strip()]
            if images:
                image_url = images[0]
        
        return cls(
            id=collection.id,
            name=collection.name,
            image_url=image_url,
            created_at=collection.created_at,
            has_scraps=latest_scrap is not None
        )
    
# 스크랩 단건 응답 (생성/조회 시 사용)
class ScrapResponse(BaseConfigModel):
    user_id: int
    restaurant_id: int
    collection_id: Optional[int] = None
    created_at: datetime

# 내 스크랩 목록용 (식당 정보 포함)
class RestaurantInfo(BaseConfigModel):
    id: int
    name: str
    category: Optional[str] = None
    address: Optional[str] = None
    image: Optional[str] = None

class MyScrapResponse(BaseConfigModel):
    restaurant: RestaurantInfo
    is_scrapped: bool = True
    
class CollectionScrapsResponse(BaseConfigModel):
    collection_name: str
    scraps: List[MyScrapResponse]    

class ScrapStatusResponse(BaseConfigModel):
    is_scrapped: bool