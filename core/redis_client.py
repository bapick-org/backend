import redis
import os
from typing import Optional
import logging

logger = logging.getLogger(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

_redis_client: Optional[redis.Redis] = None

def get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=0,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True
            )
            _redis_client.ping()
            logger.info(f"Redis connected | host={REDIS_HOST} | port={REDIS_PORT}")
        except redis.exceptions.ConnectionError as e:
            logger.error(f"Redis connection failed | host={REDIS_HOST} | error={str(e)}")
            raise ConnectionError(f"Redis 서버 연결 실패: {REDIS_HOST}:{REDIS_PORT}")
    return _redis_client