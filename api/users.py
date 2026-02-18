from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, aliased
from botocore.exceptions import ClientError
from datetime import date, time
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from typing import Optional
import logging
from core.firebase_auth import verify_firebase_token
from core.db import get_db
from core.models import User, Friendships
from core.s3 import get_s3_client, S3_BUCKET_NAME, S3_REGION 
from core.schemas import UserUpdateRequest, UserInfoResponse, PresignedUrlRequest, PresignedUrlResponse, UserSearchItemResponse, UserSearchResponse
from core.exceptions import BadRequestException, UnauthorizedException, InternalServerErrorException
from saju.saju_service import calculate_saju_and_save
from services.user_cache_service import UserCacheService

logger = logging.getLogger(__name__)
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


# GET /users: 닉네임으로 사용자 검색 
@router.get("", response_model=UserSearchResponse)
def search_users(
    keyword: Optional[str] = Query(None),
    uid: str = Depends(verify_firebase_token),
    db: Session = Depends(get_db),
):
    if not keyword:
        return UserSearchResponse(data=[], count=0)

    me = db.query(User).filter(User.firebase_uid == uid).first()
    if not me:
        logger.warning(f"Users searched rejected | actor_uid={uid} | reason=user_not_found")
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")

    FriendshipAlias = aliased(Friendships)

    query = (
        db.query(User, FriendshipAlias)
        .outerjoin(
            FriendshipAlias,
            or_(
                and_(
                    FriendshipAlias.requester_id == me.id,
                    FriendshipAlias.receiver_id == User.id,
                ),
                and_(
                    FriendshipAlias.requester_id == User.id,
                    FriendshipAlias.receiver_id == me.id,
                ),
            ),
        )
        .filter(
            User.nickname.ilike(f"%{keyword.strip()}%"),
            User.id != me.id,
        )
        .limit(50)
    )

    results = query.all()

    response_data = []

    for user_obj, friendship in results:
        relation_status = "none"

        if friendship:
            if friendship.status == "accepted":
                relation_status = "friend"
            elif friendship.status == "pending":
                if friendship.requester_id == me.id:
                    relation_status = "sent_request"
                else:
                    relation_status = "received_request"
            elif friendship.status == "rejected":
                relation_status = "none"
                
        response_data.append(
            UserSearchItemResponse(
                firebase_uid=user_obj.firebase_uid,
                nickname=user_obj.nickname,
                profile_image=user_obj.profile_image,
                relation_status=relation_status,
            )
        )

    logger.info(f"Users searched | actor_id={me.id} | keyword='{keyword}' | count={len(results)}")

    return UserSearchResponse(
        data=response_data,
        count=len(response_data),
    )


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
            logger.warning(f"User fetch rejected | actor_uid={uid} | reason=user_not_found")
            raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")
        
        # UserInfoResponse에 정의된 필드만 추출 (Pydantic을 활용한 DB 객체 직렬화)
        user_dict = UserInfoResponse.model_validate(user).model_dump(by_alias=False)
        cache_service.set_user_profile(uid, user_dict)

    # 3. 최종 필터링: 클라이언트가 요청한 필드만 추출
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
        logger.warning(f"Presigned URL generation rejected | actor_uid={uid} | reason=invalid_file_format | ext={ext}")
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
        logger.error(
            f"S3 presigned URL generation failed | actor_uid={uid} | file={file_name} | error={str(e)}",
            exc_info=True
        )
        raise InternalServerErrorException("이미지 서버 연결에 실패했습니다. 다시 시도해 주세요.")

    logger.info(f"Presigned URL generated | actor_uid={uid} | content_type={content_type} | s3_key={s3_key}")

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
        logger.warning(f"User profile update rejected | actor_uid={uid} | reason=user_not_found")
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")

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
        logger.warning(f"User profile update rejected | actor_id={user.id} | reason=invalid_date_format | value={data.birth_date}")
        raise BadRequestException("날짜 또는 시간 형식이 올바르지 않습니다.")

    # 3. 변경 사항이 있을 때만 사주 재계산
    if is_saju_data_changed:
        logger.info(f"Saju recalculation triggered | actor_id={user.id} | reason=profile_update")
        await calculate_saju_and_save(user, db)

    db.commit()
    db.refresh(user)
    cache_service.invalidate_user_profile(uid) # 캐시 무효화
    
    logger.info(f"User profile updated | actor_id={user.id}")
    return user