import json
from google import genai
from google.genai import types
from langchain_chroma import Chroma
from sqlalchemy.orm import Session
from langchain_core.documents import Document
from typing import List, Dict, Any
import time 
import re  
from core.db import SessionLocal 
from core.models import Restaurant 
from core.config import GEMMA_API_KEY 
from vectordb.vectordb_util import (
    get_chroma_client_and_collection,
    COLLECTION_NAME_RESTAURANTS,
    COLLECTION_NAME_OHAENG,
    COLLECTION_NAME_REASONS,
    embeddings
) 

# LLM API 설정
client = None
model_name = "gemini-2.5-flash"

# 지연 로드(Lazy Load) 방식으로 LLM 연결
def get_llm_client() -> genai.Client:
    global client
    if client is None:
        client = genai.Client(api_key=GEMMA_API_KEY)
    return client

SYSTEM_PROMPT = """
    당신은 식당의 카테고리, 메뉴를 분석하여 동양 철학의 오행(五行: 木, 火, 土, 金, 水) 중 해당 식당이 가장 강하게 반영하는 상위 3개의 기운을 판별하는 전문가입니다.

    응답은 오직 아래의 JSON 형식으로만 제공해야 합니다. 추론 과정이나 설명은 포함하지 않습니다.
    [
        {
            "ohaeng_type": "가장 강한 오행 기운 (예: 火)",
            "reason": "해당 오행을 선택한 간결한 이유 (15자 이내)"
        },
        {
            "ohaeng_type": "두 번째로 강한 오행 기운",
            "reason": "두 번째 오행을 선택한 간결한 이유 (15자 이내)"
        },
        {
            "ohaeng_type": "세 번째 오행 기운",
            "reason": "세 번째 오행을 선택한 간결한 이유 (15자 이내)"
        }
    ]
"""

# LLM 프롬포트 쿼리 함수
def create_user_query(restaurant_info: str, ohaeng_rules: List[Document]) -> str:
    rules_text = "\n\n".join(
        f"--- 오행: {doc.metadata.get('ohaeng_type', 'N/A')} ---\n{doc.page_content}"
        for doc in ohaeng_rules
    )

    query = f"""
        다음은 식당의 상세 정보와 오행 규칙입니다.

        ### 식당 정보
        {restaurant_info}

        ### 오행 규칙 (참고)
        {rules_text}

        이 식당이 가장 강하게 반영하는 상위 3개의 오행 기운을 판별하고, 각각에 대한 간결한 이유를 JSON 형식으로만 출력해주세요.
    """
    return query

# ChromaDB 메타데이터에 있는 오행을 MySQL 저장 포맷으로 변환
def format_chroma_reasons(metadatas: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    # 순위(ohaeng_rank)를 기준으로 정렬
    sorted_metadatas = sorted(
        metadatas, 
        key=lambda m: m.get('ohaeng_rank', 4)
    )
    
    top_3_ohaeng_data = []
    for metadata in sorted_metadatas:
        top_3_ohaeng_data.append({
            "type": metadata.get('ohaeng_type', None),
            "reason": metadata.get('reason_text', None)
        })
        
    return [d for d in top_3_ohaeng_data if d['type']] # 유효한 타입만 반환

# 식당별로 상위 오행 3개를 뽑아 저장
def add_ohang_all_restaurants():    
    print("식당별 오행 추론 시작")
    
    # 1. ChromaDB 클라이언트 연결 시도
    chroma_client, _ = get_chroma_client_and_collection(COLLECTION_NAME_RESTAURANTS) 
    if not chroma_client: return

    try:
        # 2. LangChain Chroma 객체 로드
        vectorstore_restaurants = Chroma(client=chroma_client, collection_name=COLLECTION_NAME_RESTAURANTS, embedding_function=embeddings)
        vectorstore_ohaeng = Chroma(client=chroma_client, collection_name=COLLECTION_NAME_OHAENG, embedding_function=embeddings)
        vectorstore_reasons = Chroma(client=chroma_client, collection_name=COLLECTION_NAME_REASONS, embedding_function=embeddings)

        if not vectorstore_restaurants: return
    except Exception as e:
        print(f"Chroma 컬렉션 로드 실패: {e}")
        return
    
    #모든 식당 문서 로드
    all_restaurant_docs = vectorstore_restaurants.get(
        include=['metadatas', 'documents'] 
    )
    if not all_restaurant_docs['documents']:
        print("식당 데이터 컬렉션에 문서 없음")
        return
    
    # DB 세션 생성 및 관리
    db: Session = SessionLocal()
    processed_count = 0
    total_docs = len(all_restaurant_docs['documents'])

    try:
        # MySQL에서 오행 필드에 값이 있으면 이미 저장 완료한 것이므로 넘어감
        processed_ids_query = db.query(Restaurant.id).filter(Restaurant.top_ohaeng_1.isnot(None))
        processed_ids = {row[0] for row in processed_ids_query}
        
        # 식당별로 순회하며 오행 추론
        for i, (content, metadata) in enumerate(zip(
            all_restaurant_docs['documents'], 
            all_restaurant_docs['metadatas'], 
            all_restaurant_docs['ids']
        )):
            rest_id = metadata.get('restaurant_id')
            rest_name = metadata.get('name')
            
            if not rest_id or not rest_name:
                continue
            
            # 1) MySQL에 최종 저장된 경우 스킵
            if rest_id in processed_ids:
                processed_count += 1
                continue
            
            print(f"\n[{rest_id}, {rest_name}] 오행 추론 시작 ({i+1}/{total_docs}) ---")
            
            # 2) MySQL에 저장되어 있지 않은 경우에만 추론
            try:
                # 오행 규칙 데이터와 유사도 검색: LangChain Chroma 객체에 content를 직접 전달하여 유사 문서 검색
                ohaeng_docs = vectorstore_ohaeng.similarity_search(content, k=5)
                
                # LLM 추론 쿼리 생성 및 API 호출: 오행 규칙 데이터가 전달됨
                user_query = create_user_query(content, ohaeng_docs) 
                combined_query = SYSTEM_PROMPT + "\n\n--- 식당 분석 요청 ---\n\n" + user_query
    
                # LLM API 호출 시 안정성을 위한 재시도 로직: 최대 5번 API 호출을 시도
                for attempt in range(5):
                    try:
                        # LLM API 호출
                        client = get_llm_client()
                        
                        response = client.models.generate_content(
                            model=model_name,
                            contents=[
                                # 시스템 프롬포트와 식당 분석 요청 쿼리 결합
                                types.Content(role="user", parts=[types.Part(text=combined_query)])
                            ],
                            config=types.GenerateContentConfig(temperature=0)
                        )
                        break # API 호출 성공하면 루프 탈출해 다음 로직 실행
                        
                    except Exception as e:
                        error_message = str(e)
                        # 429 에러 발생 시 (할당량 초과)
                        if "429 RESOURCE_EXHAUSTED" in error_message:
                            # 에러 메시지에서 서버가 권장하는 재시도 대기 시간(초)을 정규 표현식으로 추출
                            match = re.search(r"Please retry in (\d+\.?\d*)s", error_message)
                            # 서버 권장 시간이 있으면 사용하고, 없으면 5초
                            current_delay = float(match.group(1)) if match else 5
                            
                            # 마지막 시도가 아닌 경우 로그 출력 후 대기
                            if attempt < 5 - 1:
                                print(f"429 할당량 초과: {current_delay:.2f}초 후 재시도 (시도 {attempt + 1}/{5})")
                                time.sleep(current_delay + 1) # 서버 권장 시간보다 1초 더 기다림
                            else:
                                raise # 마지막 시도인 경우 해당 식당 건너뛰기
                        else:
                            raise # 429 외의 다른 에러인 경우에도 해당 식당 건너뛰기

                if response is None:
                    raise Exception("LLM 호출 최대 재시도 횟수 초과")
                    
                # LLM 응답 파싱
                response_text = response.text.strip()
                start = response_text.find('[')
                end = response_text.rfind(']')
                
                if start == -1 or end == -1:
                    print(f"LLM의 응답이 유효한 JSON 형식이 아님: {response_text[:50]}...")
                    continue
                    
                json_part = response_text[start:end+1]
                ohaeng_results = json.loads(json_part)
                
                # LLM 추론 결과: 상위 오행 3가지와 추론 이유
                top_3_ohaeng_data: List[Dict[str, str]] = []
                for item in ohaeng_results:
                    if 'ohaeng_type' in item and item['ohaeng_type'] in ['木', '火', '土', '金', '水']:
                        top_3_ohaeng_data.append({
                            "type": item['ohaeng_type'],
                            "reason": item.get('reason', '이유 없음').strip()
                        })
                    if len(top_3_ohaeng_data) == 3:
                        break
                
                if not top_3_ohaeng_data:
                    print("추론된 오행 기운이 없습니다.")
                    db.rollback()
                    continue
                    
                top_3_types = [d['type'] for d in top_3_ohaeng_data]
                print(f"추론된 오행: {', '.join(top_3_types)}")

                # MySQL DB 업데이트: 식당별 상위 오행 3개 저장
                restaurant = db.query(Restaurant).filter(Restaurant.id == rest_id).first()
                if restaurant:
                    restaurant.top_ohaeng_1 = top_3_types[0] if len(top_3_types) > 0 else None
                    restaurant.top_ohaeng_2 = top_3_types[1] if len(top_3_types) > 1 else None
                    restaurant.top_ohaeng_3 = top_3_types[2] if len(top_3_types) > 2 else None
                    db.commit()
                    print(f"MySQL DB 업데이트 완료: 오행 유형 저장")
                else:
                    print(f"MySQL DB 업데이트 실패: 식당 ID {rest_id}를 찾지 못함")

                # 벡터 DB 업데이트: 추론 이유를 임베딩하여 새 컬렉션에 저장
                reason_documents = []
                for idx, data in enumerate(top_3_ohaeng_data):
                    reason_content = (
                        f"식당 이름 '{rest_name}'의 {idx+1}위 오행 기운은 '{data['type']}'이며, "
                        f"그 추론 이유는 '{data['reason']}'입니다."
                    )
                    
                    reason_metadata = {
                        "restaurant_id": rest_id,
                        "name": rest_name,
                        "ohaeng_type": data['type'],
                        "ohaeng_rank": idx + 1, 
                        "reason_text": data['reason'] 
                    }
                    
                    reason_doc_id = f"{rest_id}-{idx+1}" 
                    
                    reason_documents.append(
                        Document(
                            page_content=reason_content, 
                            metadata=reason_metadata, 
                            id=reason_doc_id
                        )
                    )
                
                if reason_documents:
                    vectorstore_reasons.add_documents(reason_documents) 
                    print(f"ChromaDB 업데이트 완료: 추론 이유 {len(reason_documents)}개가 컬렉션에 저장됨")

                processed_count += 1
                
            except Exception as e:
                db.rollback() 
                print(f"LLM 호출/파싱/저장 중 오류 발생 (식당 ID: {rest_id}, 이름: {rest_name}): {e}")

    except Exception as e:
        print(f"전체 처리 중 오류 발생: {e}")
    finally:
        db.close()
        print(f"\n오행 추론 및 저장 작업 완료. 총 {processed_count}개 식당 처리.")

# 스크립트 실행
if __name__ == "__main__":
    add_ohang_all_restaurants()