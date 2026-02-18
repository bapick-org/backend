import logging
import boto3
from botocore.exceptions import NoCredentialsError
from fastapi import HTTPException
from core.config import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_REGION, AWS_S3_BUCKET_NAME

logger = logging.getLogger(__name__)

# S3 클라이언트 및 버킷 정보 설정
S3_CLIENT = None
S3_BUCKET_NAME = AWS_S3_BUCKET_NAME
S3_REGION = AWS_S3_REGION

def initialize_s3_client():
    global S3_CLIENT
    
    if not all([AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_REGION, AWS_S3_BUCKET_NAME]):
        logger.error("S3 initialization failed | reason=missing_env_vars")
        return None

    try:
        S3_CLIENT = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_S3_REGION
        )
        logger.info("S3 client initialized successfully")
        return S3_CLIENT

    except NoCredentialsError:
        logger.error("S3 initialization failed | reason=invalid_credentials")
        return None
    except Exception as e:
        logger.error(f"S3 initialization failed | error={str(e)}", exc_info=True)
        return None

def get_s3_client():
    if S3_CLIENT is None:
        initialize_s3_client()
    
    if S3_CLIENT is None:
        logger.critical("S3 access denied | reason=client_not_initialized")
        raise HTTPException(status_code=500, detail="S3 서비스에 접근할 수 없습니다.")
        
    return S3_CLIENT