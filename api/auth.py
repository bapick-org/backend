from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session
import datetime

from core.firebase_auth import verify_firebase_token
from core.db import get_db
from core.models import User
from core.schemas import RegisterRequest, GuestRegisterRequest, UserResponse
from core.exceptions import BadRequestException, ConflictException, NotFoundException, InternalServerErrorException
from saju.saju_service import calculate_saju_and_save


router = APIRouter(prefix="/auth", tags=["Auth"])

# POST /auth/signup - 회원가입 API
@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    response: Response,
    data: RegisterRequest,
    uid: str = Depends(verify_firebase_token),
    db: Session = Depends(get_db)
):
    # 1. 기존 사용자 여부 확인
    existing_user = db.query(User).filter(User.firebase_uid == uid).first()
    if existing_user:
        raise ConflictException("이미 가입된 사용자입니다.")
    
    # 2. 데이터 가공
    # birth_date 처리: 문자열 -> date 객체 변환
    try:
        birth_date = datetime.datetime.strptime(data.birthDate, "%Y-%m-%d").date()
    except ValueError:
        raise BadRequestException("날짜 형식이 올바르지 않습니다. (YYYY-MM-DD)")
    
    # birth_time 처리
    birth_time = None;
    if not data.timeUnknown:
        try:
            hour = int(data.birthHour)
            minute = int(data.birthMinute)
            if not (0 <= hour < 24) or not (0 <= minute < 60):
                raise ValueError
            birth_time = datetime.time(hour=hour, minute=minute)
        except ValueError:
            raise BadRequestException("출생 시간이 올바르지 않습니다.")

    # 3. User 객체 생성 및 저장
    user = User(
        firebase_uid=uid,
        email=data.email,
        nickname=data.nickname,
        gender=data.gender,
        birth_date=birth_date,
        birth_time=birth_time,
        birth_calendar=data.birthCalendar
    )
    
    try:
        db.add(user)
        db.commit() # 먼저 커밋해 락을 해제하고 유저 확정
        db.refresh(user)
            
        # 시간이 걸리는 사주 계산 및 저장 작업 수행
        await calculate_saju_and_save(user=user, db=db)
        
        # 사주 계산 후 유저 정보 다시 갱신
        db.commit()
    except Exception as e:
        # 하나라도 실패하면 전체 취소 (트랜잭션 롤백)
        db.rollback()
        print(f"Signup Error: {e}")
        raise InternalServerErrorException("회원가입 처리 중 오류가 발생했습니다.")
    
    # 4. 보안 쿠키 설정
    response.set_cookie(
        key="session_uid",
        value=uid,
        max_age=3600,
        httponly=True,
        secure=False,
        samesite="Lax"
    )
    
    return user


# POST /auth/login - 로그인 API
@router.post("/login", response_model=UserResponse, status_code=status.HTTP_200_OK)
async def login(
    response: Response,
    uid: str = Depends(verify_firebase_token),
    db: Session = Depends(get_db)
):
    # 1. DB에서 사용자 확인
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise NotFoundException(resource="사용자")
    
    # 2. 보안 쿠키 설정
    response.set_cookie(
        key="session_uid",
        value=uid,
        max_age=3600,
        httponly=True,
        secure=False,
        samesite="Lax"
    )

    return user


# POST /auth/guest - 게스트 회원가입 API
@router.post("/guest", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_guest(
    response: Response,
    data: GuestRegisterRequest,
    uid: str = Depends(verify_firebase_token),
    db: Session = Depends(get_db)
):
    # 1. 기존 사용자 여부 확인
    user = db.query(User).filter(User.firebase_uid == uid).first()

    # 2. 신규 게스트인 경우 가입 처리
    if not user:
        # 1) 데이터 가공
        try:
            birth_date = datetime.datetime.strptime(data.birthDate, "%Y-%m-%d").date()
        except ValueError:
            raise BadRequestException("날짜 형식이 올바르지 않습니다.")

        birth_time = None
        if not data.timeUnknown:
            try:
                hour = int(data.birthHour)
                minute = int(data.birthMinute)
                birth_time = datetime.time(hour=hour, minute=minute)
            except ValueError:
                raise BadRequestException("출생 시간이 올바르지 않습니다.")
        
        # 2) 게스트 유저 생성 및 사주 계산
        try:
            dummy_email = f"guest_{uid[:8]}@bapick.guest"
            user = User(
                firebase_uid=uid,
                email=dummy_email,
                nickname=data.nickname,
                gender=data.gender,
                birth_date=birth_date,
                birth_time=birth_time,
                birth_calendar=data.birthCalendar,
            )
            
            db.add(user)
            db.commit() # 먼저 커밋해 락을 해제하고 유저 확정
            db.refresh(user)
            
            await calculate_saju_and_save(user=user, db=db)
        except Exception as e:
            db.rollback()
            print(f"Guest Signup failed: {e}")
            raise InternalServerErrorException("게스트 계정 생성 중 오류가 발생했습니다.")

    # 3. 보안 쿠키 설정
    response.set_cookie(
        key="session_uid",
        value=uid,
        max_age=3600,
        httponly=True,
        secure=False,
        samesite="Lax"
    )

    return user