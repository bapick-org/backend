import logging
from firebase_admin import auth
from fastapi import Header
from core.exceptions import UnauthorizedException

logger = logging.getLogger(__name__)

def verify_firebase_token(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        logger.warning("Auth failed | reason=invalid_header_format")
        raise UnauthorizedException("토큰 형식이 유효하지 않습니다.")
        
    id_token = authorization.split(" ")[1].strip() # 공백 제거 추가
    
    try:
        decoded_token = auth.verify_id_token(
            id_token,
            clock_skew_seconds=5
        )
        return decoded_token["uid"]
    
    except Exception as e:        
        # 시간 오류
        if "Token used too early" in str(e):
            logger.warning(f"Auth failed | reason=token_too_early | error={e}")
            raise UnauthorizedException("인증 오류: 서버 시각 동기화에 문제가 있습니다.")
        
        # 그 외 일반적인 오류
        logger.warning(f"Auth failed | reason=invalid_token | error={e}")
        raise UnauthorizedException("유효하지 않은 토큰입니다.")
    


# WebSocket 연결 시 사용자 인증 처리 
async def get_user_uid_from_websocket_token(id_token: str) -> str:    
    if id_token.startswith("Bearer "):
        id_token = id_token.split(" ")[1].strip()
    
    try:
        decoded_token = auth.verify_id_token(
            id_token,
            clock_skew_seconds=5
        )
        uid = decoded_token["uid"]
        logger.info(f"[WS Auth] success: uid={uid}")
        return uid
    
    except auth.ExpiredIdTokenError as e:
        logger.warning(f"[WS Auth] rejected | reason=token_expired | error={str(e)}")
        raise Exception("토큰이 만료되었습니다")
    except auth.RevokedIdTokenError as e:
        logger.warning(f"[WS Auth] rejected | reason=token_revoked | error={str(e)}")
        raise Exception("토큰이 취소되었습니다")
    except Exception as e:
        logger.error(f"[WS Auth] failed | reason=unknown_error | error={str(e)}", exc_info=True)
        raise Exception(f"인증 실패: {str(e)}")