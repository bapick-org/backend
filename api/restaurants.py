import logging
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from core.firebase_auth import verify_firebase_token
from core.db import get_db
from core.models import Restaurant, RestaurantFacility, Reviews
from core.exceptions import NotFoundException, InternalServerErrorException
from core.schemas import RestaurantDetailResponse, RestaurantSearchItem, RestaurantSearchResult, NearbyRestaurantResponse
from services.restaurant_service import RestaurantLocationService
from services.restaurant_cache_service import RestaurantCacheService
from redis.exceptions import ConnectionError, TimeoutError

router = APIRouter(prefix="/restaurants", tags=["restaurants"])
logger = logging.getLogger(__name__)


# GET /restaurants/neadry: 현재 위치 근처 식당 조회 (1km 이내 리뷰 많은 순 정렬)
@router.get("/nearby", response_model=List[NearbyRestaurantResponse])
def get_nearby_restaurants(
    lat: float = Query(..., ge=-85.05, le=85.05),
    lon: float = Query(..., ge=-180, le=180),
    limit: Optional[int] = Query(5, gt=0, le=100),
    db: Session = Depends(get_db),
    uid: str = Depends(verify_firebase_token)
):    
    # 1. Redis Geo 조회
    location_service = RestaurantLocationService()
    try:
        distance_map = location_service.get_nearby_ids_with_distance(
            longitude=lon,
            latitude=lat,
            radius_km=1.0,  # 1km 반경
        )
    except (ConnectionError, TimeoutError) as e:
        logger.error(
            f"Restaurant Nearby failed | Redis connection error | User_UID: {uid} | Error: {e}",
            exc_info=True
        )
        raise InternalServerErrorException(message="위치 서비스 서버에 연결할 수 없습니다.")
    except Exception as e:
        logger.error(
            f"Restaurant Nearby failed | Unexpected geo lookup error | User_UID: {uid} | Error: {e}",
            exc_info=True
        )
        raise InternalServerErrorException(message="위치 정보를 조회하는 중 오류가 발생했습니다.")

    
    # 주변에 식당이 없으면 즉시 반환
    if not distance_map:
        logger.info(f"Restaurant Nearby | No restaurants found | User_UID: {uid} | Lat: {lat} | Lon: {lon}")
        return []
    
    restaurant_ids = list(distance_map.keys())
    
    # 2. Redis Hash 조회 (식당 정보 가져오기)
    summary_service = RestaurantCacheService()
    
    try:
        summaries = summary_service.get_summaries_by_ids(list(distance_map.keys()))
    except Exception as e:
        logger.error(
            f"Restaurant Nearby failed | Redis hash lookup error | User_UID: {uid} | Restaurant_IDs: {restaurant_ids} | Error: {e}",
            exc_info=True
        )
        raise InternalServerErrorException(message="식당 캐시 정보를 조회하는 중 오류가 발생했습니다.")


    # 3. 데이터 정합성 체크 (Geo에는 있는데 Hash에는 없는 경우)
    missing_ids = set(restaurant_ids) - set(summaries.keys())
    if missing_ids:
        logger.warning(
            f"Restaurant Nearby | Cache inconsistency detected | User_UID: {uid} | Missing_Count: {len(missing_ids)} | Missing_IDs: {missing_ids}"
        )

    # 4. 데이터 가공 및 정렬
    restaurants_data = []
    
    for r_id, summary in summaries.items():
        distance_km = distance_map.get(r_id, 0)
        
        restaurants_data.append({
            "id": r_id,
            "name": summary.get("name", "Unknown"),
            "category": summary.get("category", "-"), 
            "address": summary.get("address", ""),
            "image": summary.get("image", ""),
            "latitude": summary.get("latitude"), 
            "longitude": summary.get("longitude"),
            "rating": summary.get("rating", 0.0),
            "review_count": summary.get("review_count", 0),
            "distance_km": round(distance_km, 2),
            "distance_m": int(distance_km * 1000)
        })
    
    # '리뷰 많은 순'으로 정렬
    restaurants_data.sort(key=lambda x: x["review_count"], reverse=True)
    
    # limit 적용
    if limit:
        restaurants_data = restaurants_data[:limit]
    
    # 5. 최종 반환
    return [NearbyRestaurantResponse(**data) for data in restaurants_data]


# GET /restaurants/{id}: 특정 식당의 상세정보 조회
@router.get("/{restaurant_id}", response_model=RestaurantDetailResponse)
def get_restaurant_detail(
    restaurant_id: int, 
    db: Session = Depends(get_db),
    uid: str = Depends(verify_firebase_token)
):  
    try:
        restaurant = db.query(Restaurant).options(
            joinedload(Restaurant.menus),           
            joinedload(Restaurant.hours),
            joinedload(Restaurant.reviews),         
            joinedload(Restaurant.facility_associations).joinedload(RestaurantFacility.facility),
        ).filter(Restaurant.id == restaurant_id).first()
    except Exception as e:
        logger.error(
            f"Restaurant Detail failed | DB error | Restaurant_ID: {restaurant_id} | User_UID: {uid} | Error: {e}",
            exc_info=True
        )
        raise InternalServerErrorException(message="식당 정보를 조회하는 중 오류가 발생했습니다.")
    
    if not restaurant:
        logger.warning(f"Restaurant Detail failed | Restaurant not found | Restaurant_ID: {restaurant_id} | User_UID: {uid}")
        raise NotFoundException(resource="식당")
    
    return RestaurantDetailResponse.from_orm_custom(restaurant)


# GET /api/restaurants: 키워드로 식당/카테고리 검색
@router.get("", response_model=RestaurantSearchResult)
def get_restaurants(
    keyword: str = Query(
        ..., 
        min_length=1, 
        max_length=50, 
        description="식당 이름 또는 카테고리로 검색할 키워드 (부분 일치, 대소문자 구분 없음)"
    ), 
    limit: int = Query(
        10,
        gt=0,
        le=50,
        description="반환할 최대 결과 수 (기본값 10, 최대 50)"
    ),
    db: Session = Depends(get_db),
    uid: str = Depends(verify_firebase_token)
):
    search_term = f"%{keyword}%"
    
    try:
        results = db.query(
            Restaurant,
            Reviews.rating
        ).outerjoin(Reviews, Restaurant.id == Reviews.restaurant_id)\
        .filter(
            (Restaurant.name.ilike(search_term)) | 
            (Restaurant.category.ilike(search_term))
        )\
        .limit(limit)\
        .all()
    except Exception as e:
        logger.error(
            f"Restaurant Search failed | Keyword: {keyword} | User_UID: {uid} | Error: {e}",
            exc_info=True
        )
        raise InternalServerErrorException(message="검색 서비스에 일시적인 문제가 발생했습니다.")

    restaurants_data = []
    for res, rat in results:
        try:
            restaurants_data.append(
                RestaurantSearchItem(
                    id=res.id,
                    name=res.name,
                    category=res.category,
                    address=res.address,
                    rating=float(rat) if rat is not None else None,
                    image=res.image
                )
            )
        except Exception as conversion_error:
            logger.error(
                f"Restaurant Search failed | Data conversion error | Restaurant_ID: {res.id} | Error: {conversion_error}",
                exc_info=True
            )
            continue
        
    return RestaurantSearchResult(
        count=len(restaurants_data),
        restaurants=restaurants_data
    )