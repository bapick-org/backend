from fastapi import HTTPException
from typing import Optional

# 예외 클래스
class AppException(HTTPException):
    def __init__(
        self, 
        status_code: int, 
        code: str, 
        message: str,
        details: Optional[dict] = None
    ):
        super().__init__(status_code=status_code, detail=message)
        
        # 핸들러에서 꺼내 쓸 수 있도록 객체에 직접 저장
        self.code = code
        self.message = message
        self.extra_details = details
            


# AppException를 상속받아 구체적인 에러 정의
class NotFoundException(AppException):
    def __init__(self, resource: str = "리소스"):
        super().__init__(
            status_code=404,
            code="NOT_FOUND",
            message=f"{resource}를 찾을 수 없습니다"
        )

class BadRequestException(AppException):
    def __init__(self, message: str):
        super().__init__(
            status_code=400,
            code="BAD_REQUEST",
            message=message
        )

class UnauthorizedException(AppException):
    def __init__(self, message: str = "인증이 필요합니다"):
        super().__init__(
            status_code=401,
            code="UNAUTHORIZED",
            message=message
        )

class ForbiddenException(AppException):
    def __init__(self, message: str = "권한이 없습니다"):
        super().__init__(
            status_code=403,
            code="FORBIDDEN",
            message=message
        )

class ConflictException(AppException):
    def __init__(self, message: str):
        super().__init__(
            status_code=409,
            code="CONFLICT",
            message=message
        )
        
class InternalServerErrorException(AppException):
    def __init__(self, message: str = "서버 내부 오류가 발생했습니다."):
        super().__init__(
            status_code=500, 
            code="INTERNAL_SERVER_ERROR", 
            message=message
        )