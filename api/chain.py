import re
import random 
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
import google.genai as genai
from google.genai import types
from langchain_chroma import Chroma
from core.config import GEMMA_API_KEY
from core.models import ChatMessage, Restaurant, ChatRoom
from core.geo import calculate_distance
from saju.saju_service import get_today_saju_analysis
from vectordb.vectordb_util import get_embeddings, get_chroma_client, COLLECTION_NAME_RESTAURANTS

client = genai.Client(api_key=GEMMA_API_KEY)
model_name = "gemini-3.5-flash-lite"

embeddings = get_embeddings()
chroma_client = get_chroma_client()

vectorstore_restaurants = Chroma(
    client=chroma_client,
    collection_name=COLLECTION_NAME_RESTAURANTS,
    embedding_function=embeddings
)

# 오행별 음식 목록
OHAENG_FOOD_LISTS = {
    '목(木)': [
        "샐러드", "쌈밥", "산채비빔밥", "육회비빔밥", "샌드위치", "비빔밥", "비빔국수", "쌀국수", "수육", "보쌈", "보리밥",
    ],
    '화(火)': [
        "떡볶이", "로제떡볶이", "김치찌개", "부대찌개", "짬뽕", "제육볶음", "닭갈비", "불고기", "양념치킨", "닭강정", "삼겹살", "피자", "마라샹궈", "마파두부", "마라탕",
    ],
    '토(土)': [
        "된장찌개", "순두부찌개", "감자탕", "뼈해장국", "리조또", "카레", "오므라이스", "스테이크", "돈까스", "햄버거", "쿠키", "케이크", "파스타", "우동", "리조또", "김밥", "짜장면", "국밥",
    ],
    '금(金)': [
        "치킨", "후라이드치킨", "간장치킨", "닭백숙", "순대국", "순두부", "계란찜", "소머리국밥", "탕수육", "백반", "죽", "솥밥", "순대", "삼계탕", "곰탕",
    ],
    '수(水)': [
        "초밥", "물회", "해물찜", "오징어덮밥", "새우장", "짬뽕", "우동", "라멘", "칼국수", "만두", "어묵탕", "냉면", "소바", "잔치국수", "추어탕",
    ],
}

RECOMMENDATION_KEYWORDS = (
    "추천", "골라", "뭐 먹", "뭘 먹", "먹을거", "먹을 거",
    "다른 거", "다른 메뉴", "메뉴 알려",
)

NON_MENU_UTTERANCES = {
    "응", "어", "그래", "좋아", "네", "예", "ㅇㅇ", "오케이",
    "응 추천해줘", "추천해줘", "골라줘", "해줘",
}

AGREEMENT_UTTERANCES = {
    "응", "어", "그래", "좋아", "네", "예", "ㅇㅇ", "오케이", "해줘",
}


def is_recommendation_request(
    user_message: str,
    conversation_history: str = "",
) -> bool:
    """추천 요청 문장을 특정 메뉴 선택으로 오인하지 않도록 선판별한다."""
    normalized = re.sub(r"\s+", "", user_message).lower()
    has_recommendation_keyword = any(
        re.sub(r"\s+", "", keyword).lower() in normalized
        for keyword in RECOMMENDATION_KEYWORDS
    )
    if has_recommendation_keyword:
        return True

    normalized_history = re.sub(r"\s+", "", conversation_history).lower()
    accepted_recommendation_offer = (
        normalized in AGREEMENT_UTTERANCES
        and any(
            offer in normalized_history
            for offer in ("추천해줄까", "추천해볼까", "추천받을래")
        )
    )
    return accepted_recommendation_offer


def is_valid_menu_selection(user_message: str, menu_name: str) -> bool:
    """LLM이 만든 MENU_SELECTED 태그가 실제 메뉴 선택인지 검증한다."""
    normalized_message = re.sub(r"\s+", "", user_message).lower()
    normalized_menu = re.sub(r"\s+", " ", menu_name).strip().lower()

    if is_recommendation_request(user_message):
        return False
    if not normalized_menu or len(normalized_menu) > 30:
        return False
    if normalized_menu in NON_MENU_UTTERANCES:
        return False
    if normalized_message in {re.sub(r"\s+", "", value) for value in NON_MENU_UTTERANCES}:
        return False
    if is_recommendation_request(normalized_menu):
        return False
    return True


def _build_recommendation_fallback(oheng_info_text: str) -> str:
    """추천 요청에 모델이 잘못된 선택 태그를 반환했을 때 안전한 답변을 만든다."""
    lacking_match = re.search(r"부족한 오행:\s*([^\n]+)", oheng_info_text)
    lacking_text = lacking_match.group(1) if lacking_match else ""
    target_oheng = next(
        (name for name in OHAENG_FOOD_LISTS if name in lacking_text),
        "토(土)",
    )
    menus = random.sample(OHAENG_FOOD_LISTS[target_oheng], 3)
    return f"좋아! 오늘은 {', '.join(menus)} 어때? 마음에 드는 메뉴 하나를 골라줘."

# 사용자의 오행 상태를 기반으로 메뉴 추천 설명 메시지 생성
async def generate_oheng_explanation(uid: str, db: Session) -> str:
    # 오행 정보 가져오기
    data = await get_today_saju_analysis(uid, db)
    
    lacking_oheng = data["lacking_oheng"]
    strong_ohengs = data["strong_ohengs"]
    control_ohengs = data["control_ohengs"]
    
    # 오행별 음식 예시
    oheng_food_examples = {
        "목(木)": "샐러드, 쌈밥, 육회비빔밥 같은 신선하고 가벼운 음식",
        "화(火)": "떡볶이, 김치찌개, 짬뽕 같은 매콤하고 자극적인 음식",
        "토(土)": "김밥, 카레라이스, 된장찌개 같은 탄수화물 중심의 든든한 음식",
        "금(金)": "후라이드치킨, 두부조림, 계란찜 같은 담백하고 깔끔하거나 바삭한 음식",
        "수(水)": "초밥, 물회, 해물탕 같은 시원하고 촉촉한 음식"
    }
    
    message = "오행을 기준으로 음식을 추천하고 있어!\n\n"
    
    # 오행 기본 설명
    message += "오행이란 세상을 다섯 가지 에너지로 나눠서 이해하는 개념이야. "
    message += "우리의 몸도 화(火), 수(水), 목(木), 금(金), 토(土) 다섯 가지 기운으로 이루어져 있어서, 이 기운들의 밸런스를 맞춰주면 좋아.\n\n"
        
    # 부족한 오행
    if lacking_oheng:        
        # 각 부족한 오행별 음식 예시
        for oheng in lacking_oheng:
            food_example = oheng_food_examples.get(oheng, "관련 음식")
            message += f"오늘은 부족한 {', '.join(lacking_oheng)} 기운을 {food_example}을 통해 채우면 좋아."
        message += "\n"
    
    # 강한 오행 + 조절 오행
    if strong_ohengs and control_ohengs:
        strong_str = ', '.join(strong_ohengs)
        control_str = ', '.join(control_ohengs)

        # 상극 관계 설명
        for control in control_ohengs:
            food_example = oheng_food_examples.get(control, "관련 음식")
            message += f"넘치는 {strong_str} 기운은 {control_str} 기운의 음식({food_example})으로 눌러줄 수 있어!\n"
        message += "\n"
    
    message += "하지만 오행은 재미있는 가이드일 뿐이야. "
    message += "언제든 다른 메뉴도 찾아줄 수 있어!🍀"
    
    return message

# 오행별 음식 목록에서 랜덤으로 count개만큼만 문자열로 반환
def get_food_recommendations_for_ohaeng(oheng: str, count: int = 3) -> str:
    foods = OHAENG_FOOD_LISTS.get(oheng)
    recommended_foods = random.sample(foods, min(count, len(foods)))
    return ', '.join(recommended_foods)

def normalize_to_hangul(oheng_name: str) -> str:
    return re.sub(r'\([^)]*\)', '', oheng_name).strip()

# 오행별 일반화 설명
OHAENG_DESCRIPTION = {
    "목(木)": "상큼하고 신선한 느낌의 음식, 야채가 들어간 가벼운 메뉴",
    "화(火)": "매콤하거나 자극적인 맛의 음식",
    "토(土)": "든든하고 안정감 있는 음식",
    "금(金)": "고소하고 짭짤한 맛의 음식",
    "수(水)": "시원하고 촉촉한 느낌의 음식, 국물이나 음료류"
}

MAX_MESSAGES = 10  # 최근 대화 10개만 기억


# 오행별 음식 목록에서 랜덤으로 count개만큼만 문자열로 반환
def get_food_recommendations_for_ohaeng(oheng: str, count: int = 3) -> str:
    foods = OHAENG_FOOD_LISTS.get(oheng)
    recommended_foods = random.sample(foods, min(count, len(foods)))
    return ', '.join(recommended_foods)


# 오행 기반 메뉴 추천 메시지 생성
def generate_concise_advice(lacking_oheng: List[str], strong_oheng: List[str], control_oheng: List[str]) -> str:
    # 한글 이름을 키로, 전체 오행 이름(한자 포함)을 값으로 하는 맵 생성
    unique_ohaeng_map = {}
    for oheng in control_oheng:
        hangul_name = re.sub(r'\([^)]*\)', '', oheng).strip()
        if hangul_name and oheng in OHAENG_FOOD_LISTS: # 유효한 키인지 확인
            unique_ohaeng_map[hangul_name] = oheng
            
    unique_control_oheng = list(unique_ohaeng_map.values())
    control_oheng_str = '와 '.join(unique_control_oheng) 
    lacking_oheng_set = set(lacking_oheng)
    control_oheng_set = set(unique_control_oheng) 
    strong_oheng_str = '와 '.join(strong_oheng)
    lacking_oheng_str = '와 '.join(lacking_oheng)
    
    # 1. 부족 오행 조언
    lacking_advice = "" 
    if lacking_oheng: 
        lacking_parts = []
        for oheng in lacking_oheng:
            foods = get_food_recommendations_for_ohaeng(oheng) 
            description = OHAENG_DESCRIPTION.get(oheng, "")
            lacking_parts.append(f"{oheng} 기운이 약하니 {description}인 {foods}을(를) 추천해")
            
        lacking_foods_str = '과 '.join(lacking_parts)
        # 첫 번째 문장: 부족 오행 기운 보충 조언
        lacking_advice = lacking_foods_str + ". "
    
    
    # 2. 과다 및 제어 오행
    control_advice = ""
    # 부족 오행과 제어 오행이 겹치는지 확인
    if strong_oheng and unique_control_oheng and control_oheng_set.issubset(lacking_oheng_set):
        # 겹치는 경우
        control_advice = (
            f"특히, 부족한 {lacking_oheng_str} 기운은 강한 {strong_oheng_str}을 조절해주는 딱 맞는 상극 오행이기도 해! "
            f"따라서 {lacking_oheng_str} 기운의 음식을 먹으면 부족한 기운도 채우고, 넘치는 기운까지 잡을 수 있어 😉"
        )
    
    elif strong_oheng and unique_control_oheng:
        # 겹치지 않는 경우
        control_food_parts = []
        for oheng in unique_control_oheng: 
            foods = get_food_recommendations_for_ohaeng(oheng)
            control_food_parts.append(foods)
        control_foods_str = ', '.join(control_food_parts)
        prefix = "그리고 " if lacking_advice else "" 
        control_advice = (
            f"{prefix}강한 {strong_oheng_str} 기운은 {control_oheng_str} 기운이 눌러줄 수 있어. "
            f" 기운들이 균형을 이루게 해 줄 {control_foods_str}을 추천해."
        )

    # 3. 최종 메시지 조합
    final_message = lacking_advice + control_advice + "<br>여기서 먹고 싶은 메뉴 하나 고르면 식당까지 바로 추천해줄게!"
    return final_message

# 초기 메시지 반환
async def get_initial_chat_message(uid: str, db: Session) -> str:
    # 사주 데이터 불러오기
    data = await get_today_saju_analysis(uid, db)
    
    lacking_oheng = data["lacking_oheng"]
    strong_ohengs = data["strong_ohengs"]
    control_ohengs = data["control_ohengs"]
    
    initial_message = generate_concise_advice(
        lacking_oheng=lacking_oheng, 
        strong_oheng=strong_ohengs, 
        control_oheng=control_ohengs 
    )
    
    return initial_message


# 최근 대화 10개를 문자열로 변환
def build_conversation_history(
    db: Session,
    chatroom_id: int,
    exclude_message_id: Optional[int] = None,
) -> str:
    query = db.query(ChatMessage).filter(ChatMessage.room_id == chatroom_id)
    if exclude_message_id is not None:
        query = query.filter(ChatMessage.id != exclude_message_id)

    recent_messages = (
        query
        .order_by(ChatMessage.timestamp.desc())
        .limit(MAX_MESSAGES)
        .all()
    )
    recent_messages.reverse()  # 시간순 정렬

    conversation_history = ""
    
    for msg in recent_messages:
        if msg.message_type in ["oheng_info", "hidden_initial", "location_select"]:
            continue
        
        role = "사용자" if msg.role == "user" else "봇"
        conversation_history += f"{role}: {msg.content}\n"
    return conversation_history


# 식당 목록이 없는 경우 답변
def build_no_result(menu_name: str):
    NO_RESULT_TEMPLATE = {
        "message": "아쉽게도 **{menu_name}** 메뉴를 파는 식당을 주변 2km 내에서 찾지 못했어.😢\n\n다른 메뉴를 추천해줄까?",
        "restaurants": [],
        "count": 0
    }
    data = NO_RESULT_TEMPLATE.copy()
    data["message"] = data["message"].format(menu_name=menu_name)
    return data

# 식당 추천 - 사용자가 선택한 메뉴와 유사도 검색 + 사용자가 선택한 위치 2km 이내
def recommend_restaurants(menu_name: str, db: Session, lat: float, lon: float) -> Dict[str, Any]:    
    # 1. 검색 쿼리 정의: 사용자가 선택한 메뉴
    query_text = menu_name


def normalize_text(text: str) -> str:
    """공백 제거 + 소문자 변환 + 특수문자 기본 처리"""
    if not text:
        return ""
    return (
        text.replace(" ", "")
            .replace(",", "")
            .replace("-", "")
            .replace("_", "")
            .lower()
    )


# 유사도 검색 - 식당 정보 검색 및 추천 함수
def search_and_recommend_restaurants(menu_name: str, db: Session, lat: float=None, lon: float = None):
    # 0. 좌표 없으면 추천 불가
    if lat is None or lon is None:
        print("[ERROR] search_and_recommend_restaurants: lat/lon is None")
        return {
            "initial_message": f"'{menu_name}' 메뉴를 추천하려면 위치 정보가 필요해!",
            "restaurants": [],
            "final_message": "다른 메뉴도 추천해줄까?",
            "count": 0
        }
    

    # search_query = f"'{menu_name}' 메뉴를 판매하는 맛집 식당"

     # 1. 검색 쿼리 정의
    query_text = menu_name


    # 2. ChromaDB 연결
    embeddings = get_embeddings()
    chroma_client = get_chroma_client()

    vectorstore_restaurants = Chroma(
        client=chroma_client,
        collection_name=COLLECTION_NAME_RESTAURANTS,
        embedding_function=embeddings
    )


    try:
        restaurant_docs = vectorstore_restaurants.similarity_search(query_text, k=50)
    except Exception as e:
        print(f"Chroma 검색 오류: {e}")
        return {
            "initial_message": "식당 검색 중 오류가 발생했어.",
            "restaurants": [],
            "final_message": "다른 메뉴도 추천해줄까?",
            "count": 0
        }

    # 3. 검색 결과 없음
    if not restaurant_docs:
        return build_no_result(menu_name)
        # return {
        #     "initial_message": f"아쉽게도 **{menu_name}** 메뉴를 파는 식당을 찾지 못했어.",
        #     "restaurants": [],
        #     "final_message": "다른 메뉴도 추천해줄까?",
        #     "count": 0
        # }
        
    # 새로운 필터링 로직


    # 4. 메뉴명 기반 필터링 (content나 metadata에 메뉴명이 있는지 확인)
    restaurant_ids = []
    # chroma_results_map = {}
    chroma_map = {}
    
    menu_norm = menu_name.replace(" ", "").lower()  # 공백 제거, 소문자 변환
    
    
    for doc in restaurant_docs:
        rid = doc.metadata.get("restaurant_id")
        if not rid:
            continue
        
        # 중복 체크
        # if restaurant_id in restaurant_ids_from_chroma:
        #     continue
        content_norm = doc.page_content.replace(" ", "").lower()
        meta_norm = doc.metadata.get("menu", "").replace(" ", "").lower()

        if menu_norm in content_norm or menu_norm in meta_norm:
            if rid not in restaurant_ids:
                restaurant_ids.append(rid)
                chroma_map[rid] = doc

    if not restaurant_ids:
        return build_no_result(menu_name)
    
    
    # DB 에서 식당 정보 로드
    db_list = db.query(Restaurant).filter(Restaurant.id.in_(restaurant_ids)).all()
    db_map = {r.id: r for r in db_list}

            
    final_candidates = []
    # temp_restaurants_with_distance = []
    MAX_DIST = 2.0

    # lat, lon 변수는 원본 구조상 반드시 외부에서 주입됨 (chat.py에서)
    # 여기서는 수정하지 않고 원래 구조 유지
    for rid, doc in chroma_map.items():
        restaurant = db_map.get(rid)
        if not restaurant:
            continue

        rest_lat = getattr(restaurant, "latitude", None)
        rest_lon = getattr(restaurant, "longitude", None)
        if rest_lat is None or rest_lon is None:
            continue

        distance_km = calculate_distance(lat, lon, rest_lat, rest_lon)
        if distance_km > MAX_DIST:
            continue

        distance_m = int(round(distance_km * 1000))

        processed_image_url = None
        if restaurant.image:
            imgs = restaurant.image.split(',')
            first = imgs[0].strip()
            if first.startswith(("'", '"')) and first.endswith(("'", '"')):
                first = first[1:-1]
            if first:
                processed_image_url = first

        final_candidates.append({
            "id": restaurant.id,
            "name": restaurant.name,
            "category": restaurant.category,
            "address": restaurant.address,
            "lat": rest_lat,
            "lon": rest_lon,
            "distance_km": round(distance_km, 2),
            "distance_m": distance_m,
            "description": doc.page_content,
            "image": processed_image_url,
        })

    final_candidates.sort(key=lambda x: x["distance_km"])
    recommended = final_candidates[:3]
    
    if recommended:
        return {
            "initial_message": f"그러면 **{menu_name}** 먹으러 갈 식당 추천해줄게! 😋",
            "restaurants": recommended,
            "final_message": "다른 행운의 맛집도 추천해줄까?",
            "count": len(recommended)
        }

    return build_no_result(menu_name)

    
    
    
# 단체 채팅에서 사용자 메시지가 메뉴 추천 요청인지 감지하는 함수
def is_initial_recommendation_request(user_message: str, conversation_history: str) -> bool:
    # 대화 기록에서 봇의 상세 추천 메시지 패턴 확인
    has_bot_recommendation = bool(
        re.search(r"기운이 약하니|기운은.*조절해주는|기운으로 눌러주면", conversation_history)
    )
    
    # 봇의 추천 메시지가 있다면 return
    if has_bot_recommendation:
        return False
    
    # 추천 관련 키워드
    return is_recommendation_request(user_message, conversation_history)

# llm 호출 및 응답 반환
def generate_llm_response(
    conversation_history: str, 
    user_message: str, 
    oheng_info_text: str = ""
    ) -> str:
    recommendation_requested = is_recommendation_request(
        user_message,
        conversation_history,
    )
    recommendation_intent_rule = (
        "이번 사용자 메시지는 RECOMMEND로 확정되었다. 절대로 MENU_SELECTED 태그를 출력하지 말고, "
        "서로 다른 음식 3개를 추천한다."
        if recommendation_requested
        else "사용자 메시지를 아래 의도 규칙에 따라 판별한다."
    )

    prompt = f"""
    너는 오늘의 운세와 오행 기운에 맞춰 음식을 추천해주는 챗봇 '밥풀이'야. 
    너의 목표는 사용자의 운세에 부족한 오행 기운을 채워줄 수 있는 음식을 추천하는 거야. 
    첫 인사는 절대 반복금지. 문장은 간결하게, 다정한 친구처럼 반말로 대답해.
    
    사용자의 오행 상태는 다음과 같아:
    {oheng_info_text}

    이 오행 정보를 기반으로 사용자의 균형을 맞춰줄 수 있는 음식을 추천해야 해.
    
    
    --- 대화 기록 ---
    {conversation_history}

    --- 사용자 메시지 ---
    {user_message}

    의도 규칙:
    - RECOMMEND: 음식 추천을 요청함. "추천", "골라줘", "뭐 먹지"가 포함되면 반드시 RECOMMEND다.
    - SELECT: "김치찌개", "초밥 먹을래"처럼 특정 음식 하나를 명확히 선택함.
    - CHAT: 그 밖의 일반 대화나 "응", "그래", "좋아" 같은 짧은 동의 표현.

    현재 메시지 판별 지침: {recommendation_intent_rule}

    규칙:
    1) RECOMMEND라면 오행 정보를 반영해 음식 3개를 추천하고 MENU_SELECTED 태그를 출력하지 않는다.
    2) SELECT일 때만 반드시 아래 형식 하나로 출력한다:
    [MENU_SELECTED:사용자말한음식명]
    3) "응 추천해줘", "다른 것도 추천해줘", "뭐 먹을까?"는 RECOMMEND이며 절대 SELECT가 아니다.
    4) "응", "그래", "좋아" 자체는 음식명이 아니므로 절대 SELECT가 아니다.
    5) 판단이 불확실하면 SELECT로 판단하지 않는다.
    6) 음식 추천과 상관없는 대화라면 자연스럽게 음식 이야기로 유도한다.
    7) '@밥풀' 멘션을 언급하지 않고 자연스럽게 답변한다.
    8) 대화 기록에 실제 음식 추천이 없다면 "위에서 추천한", "아까 추천한"이라고 말하지 않는다.
    
    
    """

    response = client.models.generate_content(
        model=model_name,
        contents=[prompt],
        config=types.GenerateContentConfig(temperature=0.7)
    )

    llm_response_text = response.text.strip()

    menu_match = re.search(r"\[MENU_SELECTED:(.+?)\]", llm_response_text)
    if menu_match and not is_valid_menu_selection(user_message, menu_match.group(1)):
        if recommendation_requested:
            return _build_recommendation_fallback(oheng_info_text)
        return "어떤 메뉴가 당기는지 말해줘. 고민되면 내가 세 가지 추천해줄게!"
        
    return llm_response_text
