import logging
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session, joinedload
from datetime import date
from core.firebase_auth import verify_firebase_token
from core.db import get_db
from core.models import Reservation, Restaurant, User 
from core.exceptions import NotFoundException, UnauthorizedException, InternalServerErrorException
from core.schemas import ReservationRequest, ReservationResponse

router = APIRouter(prefix="/reservations", tags=["reservations"])
logger = logging.getLogger(__name__)


# GET /api/reservations: 예약 조회
@router.get("", response_model=list[ReservationResponse])
def get_user_reservations(
    target_date: date = Query(
        None, 
        description="조회 기준 날짜 (YYYY-MM-DD). 지정하지 않으면 전체 예약 반환"
    ),
    db: Session = Depends(get_db),
    uid: str = Depends(verify_firebase_token)
):
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        logger.warning(f"Reservation Fetch failed | User not found | UID: {uid}")
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")

    try:
        query = db.query(Reservation).options(joinedload(Reservation.restaurant)).filter(
            Reservation.user_id == user.id
        )
        if target_date:
            query = query.filter(Reservation.reservation_date == target_date)
        
        reservations = query.order_by(
            Reservation.reservation_date.desc(), 
            Reservation.reservation_time.desc()
        ).all()
        
        return [ReservationResponse.from_orm_custom(res, res.restaurant.name) for res in reservations]
    except Exception as e:
        logger.error(
            f"Reservation Fetch failed | Error retrieving reservations | User: {user.id} | Error: {e}", 
            exc_info=True
        )
        raise InternalServerErrorException(message="예약 내역을 불러오는 중 오류가 발생했습니다.")
    
    
# POST /api/reservations: 예약 생성
@router.post("", response_model=ReservationResponse)
def create_reservation(
    reservation: ReservationRequest,
    db: Session = Depends(get_db),
    uid: str = Depends(verify_firebase_token)
):
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        logger.warning(f"Reservation Create failed | User not found | UID: {uid}")
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")

    restaurant = db.query(Restaurant).filter(Restaurant.id == reservation.restaurant_id).first()
    if not restaurant:
        logger.warning(f"Reservation Create failed | Restaurant not found | ID: {reservation.restaurant_id} | User: {user.id}")
        raise NotFoundException(resource="식당")
        
    try:
        new_reservation = Reservation(
            user_id=user.id,
            restaurant_id=reservation.restaurant_id,
            reservation_date=reservation.reservation_date, 
            reservation_time=reservation.reservation_time,
            people_count=reservation.people_count
        )
        db.add(new_reservation)
        db.commit()
        db.refresh(new_reservation)
        
        logger.info(
            f"Reservation Created | ID: {new_reservation.id} | User: {user.id} | Restaurant: {restaurant.id} | Date: {reservation.reservation_date}"
        )
        return ReservationResponse.from_orm_custom(new_reservation, restaurant.name)
    except Exception as e:
        db.rollback()
        logger.error(
            f"Reservation Create failed | Error creating reservation | User: {user.id} | Error: {e}", 
            exc_info=True
        )
        raise InternalServerErrorException(message="예약 등록 중 오류가 발생했습니다.")


# PUT /api/reservations/{reservation_id}: 예약 수정 
@router.put("/{reservation_id}", response_model=ReservationResponse)
def update_reservation(
    reservation_id: int,
    reservation_update: ReservationRequest,
    uid: str = Depends(verify_firebase_token),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        logger.warning(f"Reservation Update failed | User not found | UID: {uid}")
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")
    
    reservation = db.query(Reservation).filter(
        Reservation.id == reservation_id,
        Reservation.user_id == user.id
    ).first()

    if not reservation:
        logger.warning(f"Reservation Update failed | Reservation not found | Res_ID: {reservation_id} | User: {user.id}")
        raise NotFoundException(resource="예약")

    restaurant = db.query(Restaurant).filter(Restaurant.id == reservation_update.restaurant_id).first()
    if not restaurant:
        logger.warning(f"Reservation Update failed | Restaurant not found | Rest_ID: {reservation_update.restaurant_id} | User: {user.id}")
        raise NotFoundException(resource="식당")

    try:
        reservation.reservation_date = reservation_update.reservation_date
        reservation.reservation_time = reservation_update.reservation_time
        reservation.people_count = reservation_update.people_count 
        
        db.commit()
        db.refresh(reservation)
        
        logger.info(f"Reservation Updated | ID: {reservation_id} | User: {user.id} | Rest_ID: {restaurant.id}")
        return ReservationResponse.from_orm_custom(reservation, reservation.restaurant.name)
    except Exception as e:
        db.rollback()
        logger.error(
            f"Reservation Update failed | Error updating reservation | ID: {reservation_id} | User: {user.id} | Error: {e}", 
            exc_info=True
        )
        raise InternalServerErrorException(message="예약 수정 중 오류가 발생했습니다.")


# DELETE /api/reservations/{reservation_id}: 예약 삭제
@router.delete("/{reservation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reservation(
    reservation_id: int,
    uid: str = Depends(verify_firebase_token),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        logger.warning(f"Reservation Delete failed | User not found | UID: {uid}")
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")
    
    reservation = db.query(Reservation).filter(
        Reservation.id == reservation_id,
        Reservation.user_id == user.id
    ).first()
    
    if not reservation:
        logger.warning(f"Reservation Delete failed | Reservation not found | ID: {reservation_id} | User: {user.id}")
        raise NotFoundException(resource="예약")
    
    try:
        db.delete(reservation)
        db.commit()
        logger.info(f"Reservation Deleted | ID: {reservation_id} | User: {user.id}")
        return
    except Exception as e:
        db.rollback()
        logger.error(
            f"Reservation Delete failed | Error deleting reservation | ID: {reservation_id} | User: {user.id} | Error: {e}",
            exc_info=True
        )
        raise InternalServerErrorException(message="예약 삭제 중 오류가 발생했습니다.")