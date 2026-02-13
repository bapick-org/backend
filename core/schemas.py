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
        
# S3 presigned url 생성 요청
class PresignedUrlRequest(BaseConfigModel):
    file_name: str
    content_type: str

# S3 presigned url 생성 응답
class PresignedUrlResponse(BaseConfigModel):
    presigned_url: str
    s3_key: str

# 개별 사용자 응답
class UserSearchItemResponse(BaseConfigModel):
    firebase_uid: str
    nickname: Optional[str]
    profile_image: Optional[str]
    relation_status: str

# 사용자 검색 응답
class UserSearchResponse(BaseConfigModel):
    data: List[UserSearchItemResponse]
    count: int


# --- 사주 관련 ---
class SajuAnalysisResponse(BaseConfigModel):
    headline: str = Field(..., example="금(金) 기운이 강하고, 목(木) 기운이 부족한 하루예요!")
    advice: str
    oheng_scores: Dict[str, float]
    
    class Config:
        from_attributes = True
        
        
# --- 스크랩 관련 ---
# 컬렉션 생성 요청
class CollectionCreateRequest(BaseConfigModel):
    name: str

# 컬렉션 응답
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

# 스크랩된 식당 정보
class RestaurantInfo(BaseConfigModel):
    id: int
    name: str
    category: Optional[str] = None
    address: Optional[str] = None
    image: Optional[str] = None

# 스크랩 응답 
class ScrapItemResponse(BaseConfigModel):
    restaurant: RestaurantInfo
    is_scrapped: bool = True

# 특정 컬렉션 내 스크랩 목록 응답
class CollectionScrapListResponse(BaseConfigModel):
    collection_name: str
    scraps: List[ScrapItemResponse]
    
# 스크랩 생성 요청
class ScrapCreateRequest(BaseConfigModel):
    restaurant_id: int
    collection_id: Optional[int] = None

# 스크랩 생성 응답
class ScrapCreateResponse(BaseConfigModel):
    user_id: int
    restaurant_id: int
    collection_id: Optional[int] = None
    created_at: datetime

# 스크랩 상태 응답 (특정 식당의 스크랩 여부)
class ScrapStatusResponse(BaseConfigModel):
    is_scrapped: bool
    
    
# --- 예약 관련 ---
class ReservationRequest(BaseConfigModel):
    restaurant_id: int = Field(..., description="식당 ID") 
    reservation_date: date = Field(..., description="예약 날짜 (YYYY-MM-DD)") 
    reservation_time: time = Field(..., description="예약 시간 (HH:MM:SS)") 
    people_count: int = Field(..., description="예약 인원 수")
    
class ReservationResponse(BaseConfigModel):
    id: int
    restaurant_id: int
    user_id: int
    reservation_date: date
    reservation_time: time
    people_count: int
    created_at: datetime 
    
    restaurant_name: str 

    @classmethod
    def from_orm_custom(cls, reservation, restaurant_name: str):
        return cls(
            id=reservation.id,
            restaurant_id=reservation.restaurant_id,
            user_id=reservation.user_id,
            reservation_date=reservation.reservation_date,
            reservation_time=reservation.reservation_time,
            people_count=reservation.people_count,
            created_at=reservation.created_at,
            restaurant_name=restaurant_name
        )
        
# --- 식당 관련 ---
class MenuResponse(BaseConfigModel):
    id: int
    menu_name: Optional[str] = None
    menu_price: Optional[int] = None 

class OpeningHourResponse(BaseConfigModel):
    day: Optional[str] = None
    open_time: Optional[time] = None
    close_time: Optional[time] = None
    break_start: Optional[time] = None
    break_end: Optional[time] = None
    last_order: Optional[time] = None
    is_closed: bool

class FacilityResponse(BaseConfigModel):
    id: int
    name: Optional[str] = None

class RestaurantReviewResponse(BaseConfigModel):
    id: int
    rating: Optional[float] = None
    visitor_reviews: int = 0
    blog_reviews: int = 0

# --- 식당 상세 응답 ---
class RestaurantDetailResponse(BaseConfigModel):
    id: int
    name: str
    category: str
    address: Optional[str] = None
    phone: Optional[str] = None
    image: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    
    # 관계형 데이터
    menus: List[MenuResponse] 
    hours: List[OpeningHourResponse]
    facilities: List[FacilityResponse]
    reviews: List[RestaurantReviewResponse]

    @classmethod
    def from_orm_custom(cls, restaurant):
        # 복잡한 매핑(예: 시설 정보)을 처리하는 로직
        # restaurant.facility_associations에서 facility 객체만 추출
        facility_list = [assoc.facility for assoc in restaurant.facility_associations if assoc.facility]
        
        return cls(
            id=restaurant.id,
            name=restaurant.name,
            category=restaurant.category,
            address=restaurant.address,
            phone=restaurant.phone,
            image=restaurant.image,
            latitude=restaurant.latitude,
            longitude=restaurant.longitude,
            menus=restaurant.menus,
            hours=restaurant.hours,
            facilities=facility_list,
            reviews=restaurant.reviews
        )

# --- 식당 검색 및 목록 응답 ---
class RestaurantSearchItem(BaseConfigModel):
    id: int
    name: str
    category: str
    address: Optional[str] = None
    rating: Optional[float] = None

class RestaurantSearchResult(BaseConfigModel):
    count: int
    restaurants: List[RestaurantSearchItem]
    
class NearbyRestaurantResponse(BaseConfigModel):
    id: int
    name: str
    category: str
    address: Optional[str] = None
    image: Optional[str] = None
    latitude: float
    longitude: float
    rating: Optional[float] = None
    review_count: int
    distance_km: float
    distance_m: int
    

# -- 친구 --
# 친구 개인 정보 (친구 목록용)
class FriendItemResponse(BaseConfigModel):
    firebase_uid: str
    nickname: Optional[str]
    profile_image: Optional[str]

# 친구 목록 응답
class FriendsListResponse(BaseConfigModel):
    data: List[FriendItemResponse]
    count: int

# 친구 요청 개별 정보 (요청 목록용)
class FriendRequestItemResponse(BaseConfigModel):
    id: int
    requester_uid: str
    nickname: Optional[str]
    profile_image: Optional[str]
    created_at: datetime

# 친구 요청 목록 응답
class FriendRequestsListResponse(BaseConfigModel):
    data: List[FriendRequestItemResponse]
    count: int
    
# 친구 요청 생성
class FriendRequestCreateRequest(BaseConfigModel):
    receiver_uid: str 

# 친구 요청 처리 (수락/거절)
class FriendRequestUpdateRequest(BaseConfigModel):
    action: str = Field(..., pattern="^(accept|reject)$", description="친구 요청 처리 액션. accept 또는 reject만 허용")
    
    class Config:
        json_schema_extra = {
            "example": {
                "action": "accept"
            }
        }

# 친구 요청 응답 (생성/처리 시)
class FriendRequestResponse(BaseConfigModel):
    id: int
    requester_uid: str
    receiver_uid: str
    status: str
    created_at: datetime
