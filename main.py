import os
import asyncio
import logging
import sys
import firebase_admin
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from firebase_admin import credentials
from starlette.exceptions import HTTPException as StarletteHTTPException
from core.exceptions import *
from core.schemas import ErrorResponse
from core.s3 import initialize_s3_client
from api import auth, users, chat, saju, restaurants, reservations
from api.scraps import scrap_router, collection_router
from api.friends import friends_router, friend_requests_router
from vectordb.vectordb_util import get_embeddings, get_chroma_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH, override=True)

app = FastAPI(
    title="Bapick API",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    }
)

# --- 에러 핸들러 ---
# 1. 커스텀 예외 핸들러 (AppException 및 그 자식들: NotFound, BadRequest 등)
@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(code=exc.code, message=exc.message).model_dump()
    )

# 2. Pydantic 검증 에러 핸들러 (422 -> 400 변환)
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    first_error = errors[0]
    field = first_error.get("loc")[-1]
    msg = first_error.get("msg")
    
    error_content = ErrorResponse(
        code="VALIDATION_ERROR",
        message=f"입력값이 올바르지 않습니다: {field} ({msg})"
    ).model_dump()

    return JSONResponse(status_code=400, content=error_content)

# 3. FastAPI/Starlette 기본 예외 핸들러 (URL 오타, 인증 만료 등)
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        app_exc = NotFoundException(resource="페이지")
    elif exc.status_code == 401:
        app_exc = UnauthorizedException()
    elif exc.status_code == 403:
        app_exc = ForbiddenException()
    else:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                code=f"HTTP_{exc.status_code}",
                message=exc.detail
            ).model_dump()
        )

    return JSONResponse(
        status_code=app_exc.status_code,
        content=ErrorResponse(code=app_exc.code, message=app_exc.message).model_dump()
    )

# 4. 그 외 정의되지 않은 모든 서버 에러(500) 처리
@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled Error: {exc}", exc_info=True)
    
    app_exc = InternalServerErrorException()
    
    return JSONResponse(
        status_code=app_exc.status_code,
        content=ErrorResponse(
            code=app_exc.code, 
            message=app_exc.message
        ).model_dump()
    )

# 파이어베이스 초기화
def initialize_firebase_sync():
    LOCAL_KEY_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    FIREBASE_KEY_TO_USE = None
    ENV_NAME = "Unknown"

    # 키 설정
    if LOCAL_KEY_PATH and os.path.exists(LOCAL_KEY_PATH):
        FIREBASE_KEY_TO_USE = LOCAL_KEY_PATH
        ENV_NAME = "Local Development"
    else:
        logger.warning(f"Firebase Init failed | Key file not found | Environment: {ENV_NAME}")
        
    if FIREBASE_KEY_TO_USE:
        try:
            cred = credentials.Certificate(FIREBASE_KEY_TO_USE)
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
                logger.info(f"Firebase Init success | Environment: {ENV_NAME} | Path: {FIREBASE_KEY_TO_USE}")
        except Exception as e:
            logger.critical(f"Firebase Init failed | Error: {e}", exc_info=True)
            raise RuntimeError(f"Firebase Initialization Failure: {e}")
    else:
        logger.error("Firebase Init failed | Firebase unavailable | Auth disabled")

# S3 초기화
def initialize_s3_sync():
    s3_client_info = initialize_s3_client()
    if s3_client_info:
        logger.info(f"S3 Init success | Info: {s3_client_info}")
    else:
        logger.error("S3 Init failed | Client initialization error")

# 임베딩 모델 로드 및 ChromaDB 클라이언트 연결
def initialize_vectordb_sync():
    try:
        get_embeddings()
        logger.info("VectorDB Init success | Embedding model loaded")
        get_chroma_client()
        logger.info("VectorDB Init success | ChromaDB connected")
    except Exception as e:
        logger.critical(f"VectorDB Init failed | Error: {e}", exc_info=True)
        raise RuntimeError("Vector DB Initialization Failure: Cannot start server.")

# 서버 시작 시 통합 초기화
@app.on_event("startup")
async def startup_event():
    logger.info("Server Startup initiated | Service: Bapick API")
    try:
        await asyncio.to_thread(initialize_firebase_sync)
        await asyncio.to_thread(initialize_s3_sync)
        await asyncio.to_thread(initialize_vectordb_sync)   
        logger.info("Server Startup success | All external services connected")
    except Exception as e:
        logger.critical(f"Server Startup failed | Error: {e}", exc_info=True)
        raise

# 환경 변수
ENV = os.getenv("ENV", "development")

# CORS 설정
origins = [
    "http://127.0.0.1:5500",
    "http://127.0.0.1:5501",
    "http://localhost:3000",
]

# 프로덕션 도메인은 환경에 따라 추가
if ENV == "production":
    origins.extend([
        "https://bab-front-gamma.vercel.app",
        "https://bapick-app.vercel.app",
        "https://bapick.duckdns.org",
        "https://bapick.kr",
        "https://www.bapick.kr"
    ])

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "Accept",
        "Origin",
        "User-Agent",
        "Cache-Control",
        "X-Requested-With",
    ],
    expose_headers=["Location"],
    max_age=600,  # Preflight 요청 캐싱 (10분)
)


# 라우터 등록
app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(saju.router, prefix="/api")
app.include_router(restaurants.router, prefix="/api")
app.include_router(scrap_router, prefix="/api")
app.include_router(collection_router, prefix="/api")
app.include_router(friends_router, prefix="/api")
app.include_router(friend_requests_router, prefix="/api")
app.include_router(reservations.router, prefix="/api")

# OpenAPI 커스텀 설정
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    
    openapi_schema = get_openapi(
        title="Bapick API",
        version="1.0.0",
        routes=app.routes,
    )
    
    for path in openapi_schema["paths"]:
        for method in openapi_schema["paths"][path]:
            if "422" in openapi_schema["paths"][path][method]["responses"]:
                del openapi_schema["paths"][path][method]["responses"]["422"]
                
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi