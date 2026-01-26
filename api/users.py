from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from botocore.exceptions import ClientError
from datetime import date, time
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from typing import Optional

from core.firebase_auth import verify_firebase_token
from core.db import get_db
from core.models import User
from core.s3 import get_s3_client, S3_BUCKET_NAME, S3_REGION 
from core.schemas import UserUpdateRequest, UserInfoResponse, PresignedUrlRequest, PresignedUrlResponse
from core.exceptions import BadRequestException, NotFoundException, InternalServerErrorException
from saju.saju_service import calculate_today_saju_iljin, recalculate_and_update_saju
from services.user_cache_service import UserCacheService

router = APIRouter(prefix="/users", tags=["users"])

# GET /users/me 쿼리 파라미터의 snake_case 매핑 및 요청 필드 식별용 스키마
class UserFields(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)

    # 각 필드의 요청 포함 여부만 판단하므로 bool 타입 사용
    email: Optional[bool] = None
    nickname: Optional[bool] = None
    gender: Optional[bool] = None
    birth_date: Optional[bool] = None
    birth_time: Optional[bool] = None
    birth_calendar: Optional[bool] = None
    profile_image: Optional[bool] = None
    oheng_wood: Optional[bool] = None
    oheng_fire: Optional[bool] = None
    oheng_earth: Optional[bool] = None
    oheng_metal: Optional[bool] = None
    oheng_water: Optional[bool] = None

# 쿼리 파라미터를 CamelCase에서 snake_case로 매핑
def get_requested_fields(fields: Optional[str] = Query(None)):
    if not fields:
        return set()
    
    raw_requested = [f.strip() for f in fields.split(",")]
    requested_fields = set()
    
    for field_name, field_info in UserFields.model_fields.items():
        if field_info.alias in raw_requested or field_name in raw_requested:
            requested_fields.add(field_name)
    return requested_fields


# GET /users/me - 정보 조회 API
@router.get("/me", response_model=UserInfoResponse)
async def get_my_info(
    requested_fields: set = Depends(get_requested_fields),
    uid: str = Depends(verify_firebase_token),
    db: Session = Depends(get_db)
):
    cache_service = UserCacheService()
    today = date.today()
    
    # 1. Redis에서 사용자 정보 조회
    user_dict = cache_service.get_user_profile(uid)
    user = None

    # 2. 캐시 미스 시 DB 조회 및 캐싱
    if not user_dict:
        user = db.query(User).filter(User.firebase_uid == uid).first()
        if not user:
            raise NotFoundException(resource="사용자")
        
        # UserInfoResponse에 정의된 필드만 추출 (Pydantic을 활용한 DB 객체 직렬화)
        user_dict = UserInfoResponse.model_validate(user).model_dump(by_alias=False)
    
        # 오행 계산을 위해 필요한 중간 데이터(day_sky)는 Redis 캐시에만 잠시 보관
        user_dict["day_sky"] = user.day_sky
        cache_service.set_user_profile(uid, user_dict)

    # 3. 오행 계산 여부 판단: 오행 관련 필드가 포함된 경우에만 계산 수행
    oheng_keys = {"oheng_wood", "oheng_fire", "oheng_earth", "oheng_metal", "oheng_water"}
    should_calculate_oheng = bool(requested_fields & oheng_keys)

    # 4. 오행 계산 및 캐싱
    if should_calculate_oheng:
        # 오늘 날짜에 대한 계산 결과가 이미 캐시에 있는지 확인
        cached_oheng = cache_service.get_user_today_oheng(uid, today)
        if cached_oheng:
            user_dict.update(cached_oheng)
        else:
            # 1) 캐시 히트인 경우 -> user는 None이므로 user_dict(캐시 데이터)로 복원 (db 재조회 방지)
            # 2) DB 조회 한 경우 -> user가 있으므로 복원 과정 생략
            if not user:
                user = User(**user_dict)
            try:
                iljin_data = await calculate_today_saju_iljin(user, db)
                user_dict.update(iljin_data["today_oheng_percentages"])

                # 계산 결과 하루 단위 캐싱 (불필요한 중복 계산 방지)
                cache_service.set_user_today_oheng(uid, today, iljin_data["today_oheng_percentages"])
            except Exception:
                pass # 실패 시 기본 프로필 정보만 Fallback
    
    # 5. 최종 필터링: 클라이언트가 요청한 필드만 추출
    user_dict = {k: v for k, v in user_dict.items() if k in requested_fields}

    return user_dict

# POST /users/me/presigned-url - 프로필 이미지 업로드용 Presigned URL 생성 API
@router.post("/me/presigned-url", response_model=PresignedUrlResponse)
async def generate_presigned_url(
    data: PresignedUrlRequest,
    uid: str = Depends(verify_firebase_token),
    s3_client = Depends(get_s3_client), 
):
    content_type = data.content_type
    file_name = data.file_name
    
    ext = content_type.split('/')[-1]
    if ext not in ['jpg', 'jpeg', 'png', 'webp']:
        raise BadRequestException("허용되지 않는 파일 형식입니다. (jpg, jpeg, png, webp만 가능합니다.)")
    
    s3_key = f"profile_images/{uid}_{file_name}"
    try:
        presigned_url = s3_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": S3_BUCKET_NAME,
                "Key": s3_key,
                "ContentType": content_type
            },
            ExpiresIn=3600
        )
    except ClientError as e:
        print(f"[S3 Presigned URL 생성 중 오류]: {e}")
        raise InternalServerErrorException("이미지 서버 연결에 실패했습니다. 다시 시도해 주세요.")

    return PresignedUrlResponse(presigned_url=presigned_url, s3_key=s3_key)
    

# PATCH /users/me - 정보 수정 API
@router.patch("/me", response_model=UserInfoResponse)
async def patch_my_info(
    data: UserUpdateRequest,
    uid: str = Depends(verify_firebase_token),
    db: Session = Depends(get_db)
):
    cache_service = UserCacheService()
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise NotFoundException(resource="사용자")

    is_saju_data_changed = False

    # 1. 기본 정보 수정 (닉네임, 프로필 이미지)
    if data.nickname is not None:
        user.nickname = data.nickname

    if data.profile_image_s3_key:
        user.profile_image = f"https://{S3_BUCKET_NAME}.s3.{S3_REGION}.amazonaws.com/{data.profile_image_s3_key}"
        
    # 2. 사주 데이터 수정 여부 확인 (gender, birth_date, calendar, time)
    if data.gender and user.gender != data.gender:
        user.gender = data.gender
        is_saju_data_changed = True
        
    try:
        if data.birth_date:
            new_birth_date = date.fromisoformat(data.birth_date)
            if user.birth_date != new_birth_date:
                user.birth_date = new_birth_date
                is_saju_data_changed = True
                
        if data.birth_calendar and user.birth_calendar != data.birth_calendar:
            user.birth_calendar = data.birth_calendar
            is_saju_data_changed = True
            
        # 시간 정보 처리
        new_time = None
        if data.time_unknown is False and data.birth_hour and data.birth_minute:
            new_time = time(int(data.birth_hour), int(data.birth_minute))
        
        if user.birth_time != new_time:
            user.birth_time = new_time
            is_saju_data_changed = True

    except ValueError:
        raise BadRequestException("날짜 또는 시간 형식이 올바르지 않습니다.")

    # 3. 변경 사항이 있을 때만 사주 재계산
    if is_saju_data_changed:
        await recalculate_and_update_saju(user, db)

    db.commit()
    db.refresh(user)
    cache_service.invalidate_user_profile(uid) # 캐시 무효화
    
    return user
