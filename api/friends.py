import logging
from fastapi import APIRouter, Depends, status, Query, Response
from sqlalchemy.orm import aliased, Session, joinedload
from sqlalchemy import or_, and_, case

from core.db import get_db
from core.models import User, Friendships 
from core.firebase_auth import verify_firebase_token
from core.schemas import (
    FriendRequestCreateRequest, 
    FriendRequestUpdateRequest, 
    FriendRequestResponse, 
    FriendItemResponse, 
    FriendsListResponse,
    FriendRequestItemResponse,
    FriendRequestsListResponse
)
from core.exceptions import *

logger = logging.getLogger(__name__)
friends_router = APIRouter(prefix="/friends", tags=["Friends"])
friend_requests_router = APIRouter(prefix="/friend-requests", tags=["Friend Requests"])


# 유틸리티 함수: UID -> ID 변환
def get_user_id_by_uid(db: Session, uid: str) -> int:
    """Firebase UID를 사용하여 User.id(PK)를 조회합니다."""
    user = db.query(User.id).filter(User.firebase_uid == uid).first()
    return user.id if user else None


# GET /friends: 친구 목록 조회 API (keyword에 해당하는 친구 검색도 가능)
@friends_router.get("", response_model=FriendsListResponse)
def get_friends_list(
    keyword: str | None = Query(None, description="닉네임 검색. 미입력 시 사용자의 전체 친구 목록을 반환합니다."),
    uid: str = Depends(verify_firebase_token),
    db: Session = Depends(get_db)
):
    user_id = get_user_id_by_uid(db, uid)
    if not user_id:
        logger.warning(f"Friend list fetch rejected | actor_uid={uid} | reason=user_not_found")
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")

    friend_alias = aliased(User)

    friends_query = (
        db.query(friend_alias)
        .join(
            Friendships,
            case(
                (Friendships.requester_id == user_id, Friendships.receiver_id),
                else_=Friendships.requester_id
            ) == friend_alias.id
        )
        .filter(
            Friendships.status == "accepted",
            or_(
                Friendships.requester_id == user_id,
                Friendships.receiver_id == user_id
            )
        )
    )

    if keyword:
        friends_query = friends_query.filter(
            friend_alias.nickname.ilike(f"%{keyword.strip()}%")
        )

    friends = friends_query.all()

    return FriendsListResponse(
        data=[
            FriendItemResponse(
                firebase_uid=friend.firebase_uid,
                nickname=friend.nickname,
                profile_image=friend.profile_image,
            )
            for friend in friends
        ],
        count=len(friends)
    )


# DELETE /friends/{friend_uid}: 친구 삭제 API
@friends_router.delete("/{friend_uid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_friend(
    friend_uid: str,
    uid: str = Depends(verify_firebase_token),
    db: Session = Depends(get_db)
):
    user_id = get_user_id_by_uid(db, uid)
    if not user_id:
        logger.warning(f"Friend delete rejected | actor_uid={uid} | reason=user_not_found")
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")

    friend_id = get_user_id_by_uid(db, friend_uid)

    if user_id == friend_id:
        logger.warning(f"Friend delete rejected | actor_id={user_id} | reason=self_delete_not_allowed")
        raise BadRequestException("자기 자신을 친구 목록에서 삭제할 수 없습니다.")
    
    friendship = (
        db.query(Friendships)
        .filter(
            Friendships.status == "accepted",
            or_(
                and_(
                    Friendships.requester_id == user_id,
                    Friendships.receiver_id == friend_id
                ),
                and_(
                    Friendships.requester_id == friend_id,
                    Friendships.receiver_id == user_id
                )
            )
        )
        .first()
    )

    if not friendship:
        logger.warning(
            f"Friend delete rejected | actor_id={user_id} | target_id={friend_id} | reason=friendship_not_found"
        )
        raise NotFoundException(resource="친구 관계")

    try:
        db.delete(friendship)
        db.commit()
        logger.info(f"Friend deleted | actor_id={user_id} | target_id={friend_id}")
        return
    except Exception as e:
        db.rollback()
        logger.error(
            f"Friendship delete failed | actor_id={user_id} | target_id={friend_id} | reason=db_error | error_msg={str(e)}",
            exc_info=True
        )
        raise InternalServerErrorException(message="친구 삭제 처리 중 서버 오류가 발생했습니다.")


# ========================================
# Friend Requests 리소스 (/friend-requests)
# ========================================

# GET /friend-requests: 받은 친구 요청 목록 조회 API
@friend_requests_router.get("", response_model=FriendRequestsListResponse)
def get_friend_requests(
    uid: str = Depends(verify_firebase_token),
    db: Session = Depends(get_db)
):
    user_id = get_user_id_by_uid(db, uid)
    if not user_id:
        logger.warning(f"Friend requests fetch rejected | actor_uid={uid} | reason=user_not_found")
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")

    pending_requests = (
        db.query(Friendships)
        .join(User, Friendships.requester_id == User.id)
        .filter(
            Friendships.receiver_id == user_id,
            Friendships.status == "pending"
        )
        .with_entities(
            Friendships.id.label("id"),
            User.firebase_uid.label("requester_uid"),
            User.nickname,
            User.profile_image,
            Friendships.created_at
        )
        .all()
    )

    return FriendRequestsListResponse(
        data=[
            FriendRequestItemResponse(
                id=request.id,
                requester_uid=request.requester_uid,
                nickname=request.nickname,
                profile_image=request.profile_image,
                created_at=request.created_at
            )
            for request in pending_requests
        ],
        count=len(pending_requests)
    )


# POST /friend-requests: 친구 요청 생성 API
@friend_requests_router.post("", response_model=FriendRequestResponse, status_code=status.HTTP_201_CREATED)
def create_friend_request(
    request: FriendRequestCreateRequest,
    response: Response,
    uid: str = Depends(verify_firebase_token),
    db: Session = Depends(get_db)
):
    user_id = get_user_id_by_uid(db, uid)
    if not user_id:
        logger.warning(f"Friend request rejected | actor_uid={uid} | reason=user_not_found")
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")

    receiver = db.query(User).filter(User.firebase_uid == request.receiver_uid).first()
    if not receiver:
        logger.warning(f"Friend request rejected | actor_id={user_id} | target_id={request.receiver_uid} | reason=receiver_not_found")
        raise NotFoundException(resource="사용자")
    
    receiver_id = receiver.id 
    if user_id == receiver_id:
        logger.warning(f"Friend request rejected | actor_id={user_id} | reason=self_request_not_allowed")
        raise BadRequestException("자기 자신에게는 친구 요청을 보낼 수 없습니다.")
    
    # 기존 요청 확인
    existing_request = db.query(Friendships).filter(
        or_(
            and_(Friendships.requester_id == user_id, Friendships.receiver_id == receiver_id),
            and_(Friendships.requester_id == receiver_id, Friendships.receiver_id == user_id)
        )
    ).first()

    if existing_request:
        if existing_request.status == "pending":
            logger.warning(
                f"Friend request rejected | actor_id={user_id} | target_id={receiver_id} | reason=already_pending"
            )
            raise ConflictException(message="이미 진행 중인 친구 요청이 있습니다.")
        if existing_request.status == "accepted":
            logger.warning(
                f"Friend request rejected | actor_id={user_id} | target_id={receiver_id} | reason=already_accepted"
            )
            raise ConflictException(message="이미 친구 상태입니다.")
        if existing_request.status == "rejected":
            logger.info(
                f"Deleting rejected friendship before recreating | "
                f"old_friendship_id={existing_request.id} | requester_id={user_id} | receiver_id={receiver_id}"
            )
            db.delete(existing_request)
            db.flush()
        
        
    try:
        new_request = Friendships(
            requester_id=user_id,
            receiver_id=receiver_id,
            status="pending"
        )
        db.add(new_request)
        db.commit()
        db.refresh(new_request)
        
        # Location 헤더 추가 
        response.headers["Location"] = f"/friend-requests/{new_request.id}"
        
        logger.info(f"Friend request sent | ID: {new_request.id} | actor_id: {user_id} | target_id: {receiver_id}")
        
        return FriendRequestResponse(
            id=new_request.id,
            requester_uid=uid,
            receiver_uid=request.receiver_uid,
            status=new_request.status,
            created_at=new_request.created_at
        )
    except Exception as e:
        db.rollback()
        logger.error(
            f"Friend request failed | actor_id={user_id} | target_id={receiver_id} | reason=db_error | error_msg={str(e)}", 
            exc_info=True
        )
        raise InternalServerErrorException(message="친구 요청 생성 중 서버 오류가 발생했습니다.")

    
# PATCH /friend-requests/{friendship_id}: 친구 요청 처리 API
@friend_requests_router.patch("/{friendship_id}", response_model=FriendRequestResponse)
def handle_friend_request(
    friendship_id: int,
    request: FriendRequestUpdateRequest,
    uid: str = Depends(verify_firebase_token),
    db: Session = Depends(get_db)
):
    # 1. 현재 사용자 확인
    user_id = get_user_id_by_uid(db, uid)
    if not user_id:
        logger.warning(f"Friend request handle rejected | actor_uid={uid} | reason=user_not_found")
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")

    # 2. 친구 요청 조회
    friendship = (
        db.query(Friendships)
        .options(
            joinedload(Friendships.requester), 
            joinedload(Friendships.receiver)
        )
        .filter(Friendships.id == friendship_id)
        .first()
    )
    
    if not friendship:
        logger.warning(
            f"Friend request handle rejected | actor_id={user_id} | friendship_id={friendship_id} | reason=request_not_found"
        )
        raise NotFoundException(resource="친구 요청")
    
    # 3. 권한 확인
    if friendship.receiver_id != user_id:
        logger.warning(
            f"Friend request handle rejected | actor_id={user_id} | friendship_id={friendship_id} | "
            f"receiver_id={friendship.receiver_id} | reason=unauthorized"
        )
        raise ForbiddenException("본인에게 온 친구 요청만 처리할 수 있습니다.")
    
    # 4. 상태 확인: pending 상태만 처리 가능
    if friendship.status != "pending":
        logger.warning(
            f"Friend request handle rejected | actor_id={user_id} | friendship_id={friendship_id} | "
            f"status={friendship.status} | reason=already_processed"
        )
        raise ConflictException(f"이미 {friendship.status} 처리된 요청입니다.")


    # 5. 액션 처리
    try:
        if request.action == "accept":
            friendship.status = "accepted"
            logger.info(
                f"Friend request accepted | friendship_id={friendship_id} | "
                f"requester_id={friendship.requester_id} | receiver_id={user_id}"
            )
        elif request.action == "reject":
            friendship.status = "rejected"
            logger.info(
                f"Friend request rejected | friendship_id={friendship_id} | "
                f"requester_id={friendship.requester_id} | receiver_id={user_id}"
            )
        else:
            # Pydantic validation에서 걸러지지만 방어 코드
            raise BadRequestException("유효하지 않은 action입니다. 'accept' 또는 'reject'만 가능합니다.")

        db.commit()
        db.refresh(friendship)
        
        return FriendRequestResponse(
            id=friendship.id,
            requester_uid=friendship.requester.firebase_uid,
            receiver_uid=friendship.receiver.firebase_uid,
            status=friendship.status,
            created_at=friendship.created_at
        )

    except Exception as e:
        db.rollback()
        logger.error(
            f"Friend request handle failed | actor_id={user_id} | friendship_id={friendship_id} | "
            f"action={request.action} | error={str(e)}",
            exc_info=True
        )
        raise InternalServerErrorException("친구 요청 처리 중 서버 오류가 발생했습니다.")
