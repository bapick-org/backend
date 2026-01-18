import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import firebase_admin
from firebase_admin import credentials
import os
from dotenv import load_dotenv
from api import auth, users, chat, saju, restaurants, scraps, friends, reservations
from core.s3 import initialize_s3_client
from vectordb.vectordb_util import get_embeddings, get_chroma_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH, override=True)

app = FastAPI()

# 파이어베이스 초기화
def initialize_firebase_sync():
    RENDER_KEY_PATH = "/etc/secrets/firebase-key.json"
    LOCAL_KEY_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")  # from .env
    FIREBASE_KEY_TO_USE = None
    ENV_NAME = "Unknown"

    # 1) Render 환경 키
    if os.path.exists(RENDER_KEY_PATH):
        FIREBASE_KEY_TO_USE = RENDER_KEY_PATH
        ENV_NAME = "Render Production"

    # 2) 로컬 환경 키 (.env에서 읽기)
    elif LOCAL_KEY_PATH and os.path.exists(LOCAL_KEY_PATH):
        FIREBASE_KEY_TO_USE = LOCAL_KEY_PATH
        ENV_NAME = "Local Development"

    else:
        print(f"Firebase 키 파일을 찾을 수 없습니다. 현재 환경: {ENV_NAME}")

    # 실제 초기화
    if FIREBASE_KEY_TO_USE:
        try:
            cred = credentials.Certificate(FIREBASE_KEY_TO_USE)

            # 이미 초기화된 경우 중복 초기화 방지
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
                print(f"Firebase SDK 초기화 성공 ({ENV_NAME}) 경로: {FIREBASE_KEY_TO_USE}")

        except Exception as e:
            print(f"Firebase 초기화 중 오류 발생: {e}")
            raise RuntimeError(f"Firebase 초기화 실패: {e}")
    else:
        print("경고: Firebase를 사용할 수 없습니다.")

# S3 초기화
def initialize_s3_sync():
    s3_client_info = initialize_s3_client()
    if s3_client_info:
        print(s3_client_info)
    else:
        print("S3 클라이언트 초기화 실패")

# 임베딩 모델 로드 및 ChromaDB 클라이언트 연결
def initialize_vectordb_sync():
    try:
        # 1. 양자화 모델 로드
        get_embeddings()
        print("양자화 임베딩 모델 로드 완료")

        # 2. ChromaDB 클라이언트 연결
        get_chroma_client()
        print("ChromaDB 클라이언트 연결 완료")
        
    except Exception as e:
        print(f" 벡터 DB 초기화 중 오류 발생: {e}")
        raise RuntimeError("벡터 DB 초기화 실패: 서버를 시작할 수 없습니다.")
    
# 서버 시작 시 파이어베이스, S3 초기화
@app.on_event("startup")
async def startup_event():
    try:
        await asyncio.to_thread(initialize_firebase_sync)
        await asyncio.to_thread(initialize_s3_sync)
        await asyncio.to_thread(initialize_vectordb_sync)   
    except Exception as e:
        print(f"초기화 중 오류: {e}")
        raise

# CORS 설정
origins = [
    "http://127.0.0.1:5500",
    "https://bab-front-gamma.vercel.app",
    "https://bapick-app.vercel.app",
    "https://bapick.duckdns.org",
    "https://bapick.kr",
    "https://www.bapick.kr"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 추가
app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(saju.router, prefix="/api")
app.include_router(restaurants.router, prefix="/api")
app.include_router(scraps.router, prefix="/api")
app.include_router(friends.router, prefix="/api")
app.include_router(reservations.router, prefix="/api")
