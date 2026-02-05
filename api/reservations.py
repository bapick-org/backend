from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from datetime import date
from core.firebase_auth import verify_firebase_token
from core.db import get_db
from core.models import Reservation, Restaurant, User 
from core.exceptions import NotFoundException, UnauthorizedException
from core.schemas import ReservationRequest, ReservationResponse


router = APIRouter(prefix="/reservations", tags=["reservations"])


# POST /api/reservations: 예약 생성
@router.post("/", response_model=ReservationResponse)
def create_reservation(
    reservation: ReservationRequest,
    db: Session = Depends(get_db),
    uid: str = Depends(verify_firebase_token)
):
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")

    # 식당 ID 유효성 검사
    restaurant = db.query(Restaurant).filter(Restaurant.id == reservation.restaurant_id).first()
    if not restaurant:
        raise NotFoundException(resource="식당")
        
    new_reservation = Reservation(
        user_id= user.id,
        restaurant_id=reservation.restaurant_id,
        reservation_date=reservation.reservation_date, 
        reservation_time=reservation.reservation_time,
        people_count=reservation.people_count
    )
    
    db.add(new_reservation)
    db.commit()
    db.refresh(new_reservation)

    return ReservationResponse.from_orm_custom(new_reservation, restaurant.name)



# GET /api/reservations: 예약 조회
@router.get("/", response_model=list[ReservationResponse])
def get_user_reservations(
    target_date: date = Query(None, description="조회할 특정 예약 날짜 (YYYY-MM-DD, 선택 사항)"),
    db: Session = Depends(get_db),
    uid: str = Depends(verify_firebase_token)
):
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")

    query = db.query(Reservation, Restaurant.name).join(
        Restaurant, Reservation.restaurant_id == Restaurant.id
    ).filter(
        Reservation.user_id == user.id
    )

    if target_date:
        query = query.filter(Reservation.reservation_date == target_date)
    
    reservations_with_name = query.order_by(
        Reservation.reservation_date.desc(), 
        Reservation.reservation_time.desc()
    ).all()

    return [
        ReservationResponse.from_orm_custom(res, name) 
        for res, name in reservations_with_name
    ]


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
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")
    
    reservation = db.query(Reservation).filter(
        Reservation.id == reservation_id,
        Reservation.user_id == user.id
    ).first()

    if not reservation:
        raise NotFoundException(resource="예약")
    
    restaurant = db.query(Restaurant).filter(Restaurant.id == reservation_update.restaurant_id).first()
    if not restaurant:
        raise NotFoundException(resource="식당")
    
    reservation.restaurant_id = reservation_update.restaurant_id
    reservation.reservation_date = reservation_update.reservation_date
    reservation.reservation_time = reservation_update.reservation_time
    reservation.people_count = reservation_update.people_count 
    
    db.commit()
    db.refresh(reservation)
    
    return ReservationResponse.from_orm_custom(reservation, restaurant.name)


# DELETE /api/reservations/{reservation_id}: 예약 삭제
@router.delete("/{reservation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reservation(
    reservation_id: int,
    uid: str = Depends(verify_firebase_token),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")
    
    reservation = db.query(Reservation).filter(
        Reservation.id == reservation_id,
        Reservation.user_id ==user.id
    ).first()
    
    if not reservation:
        raise NotFoundException(resource="예약")
    
    db.delete(reservation)
    db.commit()
    
    return