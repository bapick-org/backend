import json
from typing import Optional, Dict, Any
from datetime import date, time as dt_time
from core.redis_client import get_redis_client
from core.models import User
import logging

logger = logging.getLogger(__name__)

class UserCacheService:
    
    def __init__(self):
        self.redis_client = get_redis_client()
        self.user_ttl = 3600  # 1시간
        self.iljin_ttl = 86400  # 24시간 (오늘의 일진)
    
    # 1. 사용자 프로필 캐싱
    
    # 사용자 캐시 키
    def _user_cache_key(self, uid: str) -> str:
        """사용자 캐시 키"""
        return f"user:profile:{uid}"
    
    # Redis에서 사용자 프로필 조회
    def get_user_profile(self, uid: str) -> Optional[Dict]:
        try:
            key = self._user_cache_key(uid)
            data = self.redis_client.get(key)
            
            if data:
                profile = json.loads(data)
                # date/time 객체 복원
                if profile.get("birth_date"):
                    profile["birth_date"] = date.fromisoformat(profile["birth_date"])
                if profile.get("birth_time"):
                    h, m = map(int, profile["birth_time"].split(":"))
                    profile["birth_time"] = dt_time(h, m)
                
                logger.info(f"캐시 HIT: user:{uid}")
                return profile
            
            logger.info(f"캐시 MISS: user:{uid}")
            return None
            
        except Exception as e:
            logger.error(f"사용자 프로필 캐시 조회 실패: {e}")
            return None
    
    # 사용자 프로필을 Redis에 저장
    def set_user_profile(self, uid: str, user: User) -> bool:
        try:
            key = self._user_cache_key(uid)
            
            # User 객체인 경우와 dict인 경우를 구분하여 처리
            if isinstance(user, User):
                profile = {
                    "id": user.id,
                    "firebase_uid": user.firebase_uid,
                    "email": user.email,
                    "nickname": user.nickname,
                    "gender": user.gender,
                    "birth_date": user.birth_date.isoformat() if user.birth_date else None,
                    "birth_time": user.birth_time.strftime("%H:%M") if user.birth_time else None,
                    "birth_calendar": user.birth_calendar,
                    "profile_image": user.profile_image,
                    "oheng_wood": float(user.oheng_wood) if user.oheng_wood else 0.0,
                    "oheng_fire": float(user.oheng_fire) if user.oheng_fire else 0.0,
                    "oheng_earth": float(user.oheng_earth) if user.oheng_earth else 0.0,
                    "oheng_metal": float(user.oheng_metal) if user.oheng_metal else 0.0,
                    "oheng_water": float(user.oheng_water) if user.oheng_water else 0.0,
                    "day_sky": user.day_sky,
                }
            elif isinstance(user, dict):
                # dict인 경우 그대로 사용 (필요한 변환만 수행)
                profile = {
                    "email": user.get("email"),
                    "nickname": user.get("nickname"),
                    "gender": user.get("gender"),
                    "birth_date": user["birth_date"].isoformat() if isinstance(user.get("birth_date"), date) else user.get("birth_date"),
                    "birth_time": user["birth_time"].strftime("%H:%M") if isinstance(user.get("birth_time"), dt_time) else user.get("birth_time"),
                    "birth_calendar": user.get("birth_calendar"),
                    "profile_image": user.get("profile_image"),
                    "oheng_wood": float(user.get("oheng_wood", 0.0)),
                    "oheng_fire": float(user.get("oheng_fire", 0.0)),
                    "oheng_earth": float(user.get("oheng_earth", 0.0)),
                    "oheng_metal": float(user.get("oheng_metal", 0.0)),
                    "oheng_water": float(user.get("oheng_water", 0.0)),
                    "day_sky": user.get("day_sky"),
                }
            else:
                raise ValueError(f"Unsupported type for user: {type(user)}")
            
            # JSON으로 직렬화하여 저장
            self.redis_client.setex(
                key,
                self.user_ttl,
                json.dumps(profile, ensure_ascii=False)
            )
            
            logger.info(f"캐시 저장: user:{uid} (TTL: {self.user_ttl}s)")
            return True
            
        except Exception as e:
            logger.error(f"사용자 프로필 캐시 저장 실패: {e}")
            return False
    
    # 사용자 프로필 캐시 무효화 (수정 시)
    def invalidate_user_profile(self, uid: str) -> bool:
        try:
            key = self._user_cache_key(uid)
            self.redis_client.delete(key)
            logger.info(f"🗑️ 캐시 삭제: user:{uid}")
            return True
        except Exception as e:
            logger.error(f"사용자 프로필 캐시 삭제 실패: {e}")
            return False
    
    # 2. 오늘의 일진 캐싱    
    def _iljin_cache_key(self, target_date: date) -> str:
        return f"iljin:{target_date.isoformat()}"
    
    def get_today_iljin(self, target_date: date) -> Optional[Dict]:
        try:
            key = self._iljin_cache_key(target_date)
            data = self.redis_client.get(key)
            
            if data:
                logger.info(f"일진 캐시 HIT: {target_date}")
                return json.loads(data)
            
            logger.info(f"일진 캐시 MISS: {target_date}")
            return None
            
        except Exception as e:
            logger.error(f"일진 캐시 조회 실패: {e}")
            return None
    
    def set_today_iljin(self, target_date: date, iljin_data: Dict) -> bool:
        try:
            key = self._iljin_cache_key(target_date)
            
            # 24시간 캐싱 (자정 지나면 자동 삭제)
            self.redis_client.setex(
                key,
                self.iljin_ttl,
                json.dumps(iljin_data, ensure_ascii=False)
            )
            
            logger.info(f"일진 캐시 저장: {target_date}")
            return True
            
        except Exception as e:
            logger.error(f"일진 캐시 저장 실패: {e}")
            return False
    
    # 3. 사용자별 오늘의 오행 점수 캐싱    
    def _user_today_oheng_key(self, uid: str, target_date: date) -> str:
        return f"user:oheng:{uid}:{target_date.isoformat()}"
    
    def get_user_today_oheng(self, uid: str, target_date: date) -> Optional[Dict]:
        try:
            key = self._user_today_oheng_key(uid, target_date)
            data = self.redis_client.get(key)
            
            if data:
                logger.info(f"오행 캐시 HIT: {uid} - {target_date}")
                return json.loads(data)
            
            return None
            
        except Exception as e:
            logger.error(f"오행 캐시 조회 실패: {e}")
            return None
    
    def set_user_today_oheng(self, uid: str, target_date: date, oheng_data: Dict) -> bool:
        try:
            key = self._user_today_oheng_key(uid, target_date)
            
            # 24시간 캐싱
            self.redis_client.setex(
                key,
                self.iljin_ttl,
                json.dumps(oheng_data, ensure_ascii=False)
            )
            
            logger.info(f"오행 캐시 저장: {uid} - {target_date}")
            return True
            
        except Exception as e:
            logger.error(f"오형 캐시 저장 실패: {e}")
            return False