from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from datetime import datetime
from sqlalchemy.orm import joinedload
from core.db import get_db
from core.firebase_auth import verify_firebase_token
from core.models import User, Scrap, Collection
from core.exceptions import NotFoundException, ConflictException, UnauthorizedException
from core.schemas import ScrapCreate, CollectionCreate, CollectionResponse, MyScrapResponse, ScrapResponse, CollectionScrapsResponse, ScrapStatusResponse

router = APIRouter()

scrapRouter = APIRouter(prefix="/scraps", tags=["scraps"])
collectionRouter = APIRouter(prefix="/collections", tags=["collections"])


# GET /api/collections: 전체 컬렉션 목록 조회 - 각 컬렉션 정보와 대표 이미지 포함
@collectionRouter.get("", response_model=list[CollectionResponse])
def get_my_collections(
    db: Session = Depends(get_db),
    uid: str = Depends(verify_firebase_token)
):
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")
    
    response_list = []

    # "모든 스크랩" 가상 컬렉션 생성: 해당 유저의 스크랩 중 가장 최근 스크랩의 식당 이미지 가져오기
    total_latest_scrap = db.query(Scrap)\
        .options(joinedload(Scrap.restaurant))\
        .filter(Scrap.user_id == user.id)\
        .order_by(Scrap.created_at.desc())\
        .first()

    mock_all_collection = Collection(
        id=0, 
        name="모든 스크랩", 
        created_at=datetime.utcnow()
    )

    all_card = CollectionResponse.from_orm_custom(mock_all_collection, total_latest_scrap)
    all_card.is_system_default = True # 시스템 기본 카드로 표시
    response_list.append(all_card)
    
    collections = db.query(Collection)\
        .filter(Collection.user_id == user.id)\
        .order_by(Collection.created_at.desc())\
        .all()
        
    for collection in collections:
        # 가장 최근 스크랩 1개만 가져옴
        latest_scrap = db.query(Scrap)\
            .options(joinedload(Scrap.restaurant))\
            .filter(Scrap.collection_id == collection.id)\
            .order_by(Scrap.created_at.desc())\
            .first()

        response_list.append(
            CollectionResponse.from_orm_custom(collection, latest_scrap)
        )
        
    return response_list


# POST /api/collections: 컬렉션 생성
@collectionRouter.post("", response_model=CollectionResponse, status_code=status.HTTP_201_CREATED)
def create_user_collection(
    collection_data: CollectionCreate,
    db: Session = Depends(get_db),
    uid: str = Depends(verify_firebase_token)
):
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")

    existing_collection = db.query(Collection).filter(
        Collection.user_id == user.id,
        Collection.name == collection_data.name
    ).first()
    if existing_collection:
        raise ConflictException(message="동일한 이름의 컬렉션이 이미 존재합니다.")

    new_collection = Collection(
        user_id=user.id,
        name=collection_data.name,
        created_at=datetime.utcnow()
    )
    
    db.add(new_collection)
    db.commit()
    db.refresh(new_collection)

    return {
        "id": new_collection.id,
        "name": new_collection.name,
        "image_url": "",
        "created_at": new_collection.created_at,
        "has_scraps": False
    }
    
    
# DELETE /api/collections/{id}: 특정 컬렉션 삭제
@collectionRouter.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_collection(
    collection_id: int,
    db: Session = Depends(get_db),
    uid: str = Depends(verify_firebase_token)
):
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")

    collection = db.query(Collection).filter(
        Collection.id == collection_id,
        Collection.user_id == user.id
    ).first()

    if not collection:
        raise NotFoundException(resource="컬렉션")

    # 내부 스크랩 처리 (참조 무결성 유지)
    # 컬렉션은 사라져도 스크랩은 남아야 하므로 collection_id를 NULL로 업데이트
    # synchronize_session=False: 성능 최적화 목적 (삭제 이후에 해당 객체를 파이썬에서 다시 읽어서 작업할 일이 없으므로)
    db.query(Scrap).filter(
        Scrap.collection_id == collection_id,
        Scrap.user_id == user.id
    ).update({Scrap.collection_id: None}, synchronize_session=False)

    db.delete(collection)
    db.commit()

    return


# GET /api/collections/{id}/scraps: 특정 컬렉션 내 스크랩 목록 조회
@collectionRouter.get("/{collection_id}/scraps", response_model=CollectionScrapsResponse)
def get_scraps_in_collection(
    collection_id: int,
    db: Session = Depends(get_db),
    uid: str = Depends(verify_firebase_token)
):
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")

    # 1. 컬렉션 존재 여부 및 이름 확인
    collection = db.query(Collection).filter(
        Collection.id == collection_id,
        Collection.user_id == user.id
    ).first()

    if not collection:
        raise NotFoundException(resource="컬렉션")

    # 2. 해당 컬렉션의 스크랩 목록 조회
    # joinedload를 통해 restaurant 정보를 미리 가져옴
    scraps = db.query(Scrap)\
        .options(joinedload(Scrap.restaurant))\
        .filter(Scrap.user_id == user.id, Scrap.collection_id == collection_id)\
        .order_by(Scrap.created_at.desc())\
        .all()
        
    # 3. 최종 반환
    return {
        "collection_name": collection.name,
        "scraps": scraps
    }



# GET /api/scraps: 전체 스크랩 목록 조회
@scrapRouter.get("", response_model=list[MyScrapResponse])
def get_my_scraps(
    db: Session = Depends(get_db),
    uid: str = Depends(verify_firebase_token)
):
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")

    # 스크랩 확인: joinedload를 통해 restaurant 정보를 미리 가져옴
    scraps = db.query(Scrap)\
        .options(joinedload(Scrap.restaurant))\
        .filter(Scrap.user_id == user.id)\
        .order_by(Scrap.created_at.desc())\
        .all()

    # 최종 반환 (Pydantic이 'restaurant' 필드를 찾아 RestaurantInfo 스키마로 자동 매핑함)
    return scraps

# POST /api/scraps/restaurants/{id}: 식당 스크랩 추가
@scrapRouter.post("/restaurants/{restaurant_id}", response_model=ScrapResponse, status_code=status.HTTP_201_CREATED)
def create_scrap(
    scrap_data: ScrapCreate,
    db: Session = Depends(get_db),
    uid: str = Depends(verify_firebase_token)
):    
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")

    existing_scrap = db.query(Scrap).filter(
        Scrap.user_id == user.id,
        Scrap.restaurant_id == scrap_data.restaurant_id
    ).first()

    if existing_scrap:
        raise ConflictException(message="이미 스크랩된 식당입니다.")

    new_scrap = Scrap(
        user_id=user.id,
        restaurant_id=scrap_data.restaurant_id,
        collection_id=scrap_data.collection_id,
        created_at=datetime.utcnow()
    )
    
    db.add(new_scrap)
    db.commit()
    db.refresh(new_scrap)

    return new_scrap


# GET /api/scraps/restaurants/{id}: 식당 스크랩 상태 확인
@scrapRouter.get("/restaurants/{restaurant_id}", response_model=ScrapStatusResponse)
def get_scrap_status(
    restaurant_id: int,
    db: Session = Depends(get_db),
    uid: str = Depends(verify_firebase_token)
):
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")

    scrap = db.query(Scrap).filter(
        Scrap.user_id == user.id,
        Scrap.restaurant_id == restaurant_id
    ).first()

    return {"is_scrapped": bool(scrap)}


# DELETE /api/scraps/restaurants/{id}: 식당 스크랩 삭제
@scrapRouter.delete("/restaurants/{restaurant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scrap(
    restaurant_id: int,
    db: Session = Depends(get_db),
    uid: str = Depends(verify_firebase_token)
):
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise UnauthorizedException("유효하지 않은 사용자 정보입니다.")

    scrap = db.query(Scrap).filter(
        Scrap.user_id == user.id,
        Scrap.restaurant_id == restaurant_id
    ).first()
    if not scrap:
        raise NotFoundException(resource="스크랩 정보")

    db.delete(scrap)
    db.commit()
    
    return



router.include_router(scrapRouter)
router.include_router(collectionRouter)
