import logging
import requests
from math import radians, sin, cos, sqrt, atan2
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# 주소/장소명을 위도, 경도 좌표로 변환
def geocode_location(query: str) -> Tuple[Optional[float], Optional[float]]:
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {"q": query, "format": "json", "limit": 1}
        headers = {'User-Agent': 'BapickChatBot/1.0'}
        
        response = requests.get(url, params=params, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if data:
                return float(data[0]['lat']), float(data[0]['lon'])
            logger.warning(f"Geocoding no result | query={query}")
    except Exception as e:
        logger.error(f"Geocoding system error | query={query} | error={str(e)}", exc_info=True)
        
    return None, None

# 두 좌표 간 거리를 km 단위로 계산
def calculate_distance(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0 # 지구 반지름 (km)
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    
    return R * c
