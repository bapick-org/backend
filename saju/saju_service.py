import random
import logging
from sqlalchemy.orm import Session
from datetime import date, time, timedelta, datetime
from typing import Dict, List, Optional, Tuple
from sqlalchemy import desc
from collections import Counter
from starlette.concurrency import run_in_threadpool

from core.models import User, Manse 
from core.exceptions import BadRequestException, NotFoundException, InternalServerErrorException
from saju.saju_data import get_time_ju_data, get_time_ju_data2, get_ten_star, get_jijangan, get_five_circle_from_char

logger = logging.getLogger(__name__)


OHENG_KOREAN_KEYS = ["목(木)", "화(火)", "토(土)", "금(金)", "수(水)"]

# DB 필드와 매핑이 필요한 경우
KOREAN_TO_DB_MAP = {
    "목(木)": "oheng_wood",
    "화(火)": "oheng_fire",
    "토(土)": "oheng_earth",
    "금(金)": "oheng_metal",
    "수(水)": "oheng_water"
}

def _get_manse_record(
    db: Session,
    birth_date: date,
    birth_time: Optional[time],
    birth_calendar: str
) -> Optional[Manse]:
    """
    출생 정보(생년월일시, 양/음력)를 바탕으로
    사주 계산에 사용할 '일주 기준 날짜'를 결정하고,
    해당 날짜에 대응하는 만세력(Manse) 레코드를 조회한다.

    - 자시(子時, 23:30 ~ 01:29) 출생 시 일주 기준 날짜를 다음 날로 보정
    - 절입 시각 이전 출생 시 월주·년주를 이전 절기 기준으로 보정
    """

    # 1. 자시 규칙 적용: 일주 계산에 사용할 기준 날짜 결정
    search_date = birth_date
    if birth_time and birth_time >= time(23, 30):
        search_date += timedelta(days=1)

    # 2. 기준 날짜에 해당하는 Manse 레코드 조회
    if birth_calendar == "solar":
        manse_record = db.query(Manse).filter(
            Manse.solarDate == search_date
        ).first()

    elif birth_calendar.startswith("lunar"):
        is_leap_month = 1 if birth_calendar == "lunar_leap" else 0
        manse_record = db.query(Manse).filter(
            Manse.lunarDate == search_date,
            Manse.leapMonth == is_leap_month
        ).first()
    else:
        raise BadRequestException("birth_calendar 값이 올바르지 않습니다.")

    if not manse_record:
        raise NotFoundException(resource="만세력")

    # 3. 절입 규칙 적용: 월주/년주를 절기 기준으로 보정
    if manse_record.seasonStartTime and birth_time:
        birth_datetime = datetime.combine(birth_date, birth_time)
        
        if birth_datetime < manse_record.seasonStartTime:
            previous_record = db.query(Manse).filter(
                Manse.solarDate < manse_record.solarDate
            ).order_by(desc(Manse.solarDate)).first()

            if previous_record:
                manse_record.yearSky = previous_record.yearSky
                manse_record.yearGround = previous_record.yearGround
                manse_record.monthSky = previous_record.monthSky
                manse_record.monthGround = previous_record.monthGround

    return manse_record


def get_time_pillar(day_sky: str, birth_time: Optional[time]) -> Dict[str, Optional[str]]:
    """
    일간(日干)과 출생 시각을 기준으로 시주(時柱: 시간의 천간·지지)를 계산한다.

    - 출생 시각이 없으면 시주는 계산하지 않는다.
    - 시지는 24시간을 12시진(子~亥)으로 나누어 결정한다.
    - 시간은 일간과 시지의 조합 규칙에 따라 결정한다.
    """

    # 출생 시각을 모르는 경우: 시주 계산 불가
    if birth_time is None:
        return {'time_sky': None, 'time_ground': None}

    # --- 1. 시지(時支) 계산: 출생 시각을 12시진 중 하나로 매핑 ---
    time_ju_data = get_time_ju_data() 
    time_index = None

    for index, time_range in time_ju_data.items():
        start_time, end_time = time_range[0], time_range[1]
        
        # 자시(子時, 23:30 ~ 01:29)는 날짜를 넘어가므로 별도 처리
        if start_time > end_time: 
            if birth_time >= start_time or birth_time <= end_time:
                time_index = index
                break
        elif start_time <= birth_time <= end_time:
            time_index = index
            break
    
    # 어떤 시진에도 해당하지 않는 경우 (비정상 입력 또는 데이터 오류)
    if time_index is None:
        return {'time_sky': None, 'time_ground': None}

    # --- 2. 시간(時干) 계산: 일간과 시지 인덱스에 따른 규칙 적용 ---
    time_ju_data_2 = get_time_ju_data2()
    
    # 규칙 테이블: time_ju_data_2[일간][시지_인덱스] = [천간, 지지]
    if day_sky in time_ju_data_2 and time_index in time_ju_data_2[day_sky]:
        pillar_data = time_ju_data_2[day_sky][time_index]
        return {'time_sky': pillar_data[0], 'time_ground': pillar_data[1]}
    
    # 규칙 테이블에 정의되지 않은 경우
    return {'time_sky': None, 'time_ground': None} 


def calculate_oheng_score(saju_pillars: Dict[str, Optional[str]]) -> Dict[str, float]:
    """
    사주 팔자(8글자)를 구성하는 천간·지지를 기준으로
    오행(木火土金水)의 비율(%)을 계산한다.

    계산 원칙:
    - 천간: 현상적인 기운으로 전체 비중의 30%
    - 지지: 근본적인 기운으로 전체 비중의 70%
    - 월지: 계절을 결정하는 '월지'에 30% 추가 가중치 부여
    - 지장간 반영: 지지의 점수는 해당 지지의 지장간 비율에 따라 분배
    """

    # --- 1. 가중치 기준 및 초기 설정 ---
    TOTAL_SCORE = 100.0
    SKY_SCORE_TOTAL = TOTAL_SCORE * 0.3         # 천간: 30%
    GROUND_SCORE_TOTAL = TOTAL_SCORE * 0.7      # 지지: 70%
    MONTH_BONUS = 0.3                           # 월지 가중치 비율 (70점의 30%인 21점 추가)

    sky_base_score = SKY_SCORE_TOTAL / 4.0       # 천간 1개당 기본 점수 (7.5점)
    ground_base_score = GROUND_SCORE_TOTAL / 4.0 # 지지 1개당 기본 점수 (17.5점)
    
    # 결과 저장용 딕셔너리 초기화 (oheng_wood, oheng_fire 등)
    scores = {v: 0.0 for v in OHENG_KOREAN_KEYS}
    
    # --- 2. 필수 데이터 조회 ---
    day_sky = saju_pillars.get('day_sky')
    if not day_sky:
        raise InternalServerErrorException("사용자의 일간 정보가 누락되었습니다. 사주 데이터 무결성 오류입니다.")

    ten_star_data = get_ten_star().get(day_sky)   # 일간 기준 천간 → 오행 매핑
    jijangan_data = get_jijangan()                # 지지별 지장간(지지 안에 숨어 있는 오행) 구성 정보
    if not ten_star_data:
        raise InternalServerErrorException(f"일간({day_sky})에 대한 천간 오행 매핑 데이터가 존재하지 않습니다.")
    if not jijangan_data:
        raise InternalServerErrorException("지장간 기준 데이터가 존재하지 않습니다.")
        

    # --- 3. 천간 점수 계산 ---
    # 각 천간의 오행을 확인해 해당 오행에 점수 분배
    for field in ['year_sky', 'month_sky', 'day_sky', 'time_sky']:
        char = saju_pillars.get(field)
        if char and (info := ten_star_data.get(char)):
            kor_name = info[1]
            if kor_name in scores:
                scores[kor_name] += sky_base_score

    # --- 4. 지지 점수 계산 ---
    # 지지는 여러 오행이 혼합된 구조이므로, 지장간(오행 구성 비율)에 따라 오행 점수 분배
    for field in ['year_ground', 'month_ground', 'day_ground', 'time_ground']:
        char = saju_pillars.get(field)
        if not char or char not in jijangan_data:
            continue

        # 해당 지지의 기본 배정 점수
        current_weight = ground_base_score
        if field == 'month_ground':
            # 월지는 추가 가중치 부여
            current_weight += (GROUND_SCORE_TOTAL * MONTH_BONUS)

        # 해당 지지의 지장간 구성 확인
        contents = [v for v in jijangan_data[char].values() if v]
        total_rate = sum(float(c.get("rate", 0)) for c in contents)
        
        # 지장간 비율에 따라 오행 점수 분배
        for content in contents:
            kor_name = content.get("fiveCircle")
            rate = float(content.get("rate", 0))
            if kor_name in scores and total_rate > 0:
                scores[kor_name] += current_weight * (rate / total_rate)

    # --- 5. 최종 점수를 비율(%)로 변환 --- 
    total_sum = sum(scores.values())
    if total_sum == 0: return scores
    
    # 총점을 100% 기준으로 환산
    return {k: round((v / total_sum) * 100, 1) for k, v in scores.items()}

    

async def calculate_saju_and_save(
    user: User,
    db: Session,
    commit: bool = True
) -> Dict[str, float]:
    """
    사용자의 출생 정보를 기반으로 사주팔자와 오행 비율을 계산하고,
    계산 결과를 Users 테이블에 저장한다.

    처리 흐름:
    1. 생년월일 검증
    2. 사주 팔자 계산
    2-1. 년주/월주/일주 확보 (만세력 조회)
    2-2. 시주 계산
    3. 오행 비율 계산
    4. 계산 결과를 DB에 저장
    """

    # --- 1. 생년월일 검증 ---
    if not user.birth_date:
        raise BadRequestException("사주 계산에 필요한 생년월일 정보가 부족합니다.")
        
    # --- 2. 사주 팔자 계산 ---
    manse_record = await run_in_threadpool(
        _get_manse_record, db, user.birth_date, user.birth_time, user.birth_calendar
    )
    
    if not manse_record:
        calendar_type = "음력" if user.birth_calendar == "LUNAR" else "양력"
        error_detail = f"{user.birth_date} ({calendar_type})"
        
        raise NotFoundException(
            resource=f"만세력 데이터 ({error_detail})"
        )
    
    # 시주 계산
    if user.birth_time:
        time_pillar = get_time_pillar(manse_record.daySky, user.birth_time)
    else:
        time_pillar = {"time_sky": None, "time_ground": None}
        
    # 사주팔자(연·월·일·시) 구성
    saju_pillars = {
        'year_sky': manse_record.yearSky,
        'year_ground': manse_record.yearGround,
        'month_sky': manse_record.monthSky,
        'month_ground': manse_record.monthGround,
        'day_sky': manse_record.daySky,
        'day_ground': manse_record.dayGround,
        'time_sky': time_pillar['time_sky'],
        'time_ground': time_pillar['time_ground'],
    }

    # --- 3. 오행 비율 계산 ---
    oheng_percentages = calculate_oheng_score(saju_pillars)
    logger.debug(
        f"Oheng calculation | uid={user.firebase_uid} | scores={oheng_percentages}"
    )

    # --- 4. 오행 비율을 DB에 저장 ---
    for kor_key, db_col in KOREAN_TO_DB_MAP.items():
        setattr(user, db_col, oheng_percentages.get(kor_key, 0.0))
    
    user.day_sky = saju_pillars['day_sky']
    
    if commit:
        await run_in_threadpool(db.commit)
        await run_in_threadpool(db.refresh, user)
    
    return oheng_percentages


async def calculate_today_saju_iljin(
    user: User,
    db: Session
) -> Dict: 
    """
    사용자의 타고난 사주(일간·오행 비율)에
    오늘의 일진(日辰)을 반영한 오행 비율을 계산한다.

    처리 흐름:
    - 사용자 일간(day_sky) 및 오행 데이터 조회
    - 오늘의 일진(일간·일지) 조회
    - 일간 기준 십신 계산
    - 오늘의 기운을 오행 비율에 가중치로 반영
    """
    
    # --- 1. 기본 검증 ---
    db_columns = list(KOREAN_TO_DB_MAP.values())
    if all(getattr(user, col) is None for col in db_columns):
        raise InternalServerErrorException("사용자의 오행 비율 데이터가 존재하지 않습니다. 먼저 사주 계산이 필요합니다.")

    if not user.day_sky: 
        raise InternalServerErrorException("사용자의 일간 정보가 누락되었습니다. 사주 데이터 무결성 오류입니다.")
    
    # DB에서 오행 비율 조회
    current_scores = {
        kor_key: float(getattr(user, db_col) or 0.0)
        for kor_key, db_col in KOREAN_TO_DB_MAP.items()
    }

    # --- 2. 오늘의 일진 조회 ---
    today_manse = await run_in_threadpool(
        lambda: db.query(Manse).filter(Manse.solarDate == date.today()).first()
    )
    if not today_manse:
        raise NotFoundException(resource="오늘의 일진")

    # --- 3. 오행 비율 보정: 오늘의 기운에 가중치 반영 ---
    WEIGHTS = {"sky": 20.0, "ground": 20.0}
    today_chars = {"sky": today_manse.daySky, "ground": today_manse.dayGround}

    for part in ["sky", "ground"]:
        char = today_chars[part]
        kor_oheng = get_five_circle_from_char(char)
        if kor_oheng in current_scores:
            current_scores[kor_oheng] += WEIGHTS[part]

    # --- 4. 100%로 재정규화 ---
    total_sum = sum(current_scores.values()) 

    return {
        k: round((v / total_sum) * 100, 2) 
        for k, v in current_scores.items()
    }
    
    


def classify_and_determine_recommendation(
    oheng_scores: Dict[str, float]
) -> Dict[str, str | List[str]]:
    """
    사용자의 오행 비율을 기반으로 유형을 분류하고, 보충/억제 오행을 결정한다.
    """
    # 오행 유형 기준 임계값 정의
    THRESHOLD_MUHANG: float = 5.0          # 무형 기준: 특정 오행이 5% 미만일 경우
    THRESHOLD_MAX_MIN_DIFF: float = 10.0   # 균형형 기준: 최대-최소 오행 차이가 10% 이하일 경우
    
    # 1. 오행 비율 조회    
    vals = list(oheng_scores.values())
    min_val = min(vals)
    max_val = max(vals)
    max_diff = max_val - min_val

    # 2. 오행 유형 분류
    if min_val < THRESHOLD_MUHANG:
        oheng_type = "무형"
    elif max_diff <= THRESHOLD_MAX_MIN_DIFF:
        oheng_type = "균형형"
    else:
        oheng_type = "치우침형"
    
    # 3. 보충 오행 / 억제 오행 선정
    lacking_oheng = [name for name, val in oheng_scores.items() if val == min_val]
    strong_oheng = [name for name, val in oheng_scores.items() if val == max_val]
            
    return {
        "oheng_type": oheng_type,
        "primary_supplement_oheng": lacking_oheng,
        "secondary_control_oheng": strong_oheng
    }
        
        
OHENG_ATTRIBUTES = {
    '목(木)': {'food': '채소류, 신맛 음식'},
    '화(火)': {'food': '매운맛, 쓴맛 음식'},
    '토(土)': {'food': '곡물류, 단맛 음식'},
    '금(金)': {'food': '육류, 바삭한 음식'},
    '수(水)': {'food': '해산물, 짠맛 음식'},
}

CLOSING_MESSAGES = [
    "마음속까지 따뜻해지는 평온한 하루를 보낼 거예요!",
    "긍정적인 에너지가 온몸에 가득 찰 거예요!",
    "오늘이야말로 행운의 주인공이 되는 날이 될 거예요!",
    "평온한 하루가 될 거예요!",
    "순조로운 하루를 보낼 거예요!",
]

# 상극 관계 (목->토, 화->금, 토->수, 금->목, 수->화)
COUNTER_OHENG_MAP = {
    '목(木)': '금(金)',
    '화(火)': '수(水)',
    '토(土)': '목(木)',
    '금(金)': '화(火)',
    '수(水)': '토(土)',
}

# 음식 정보를 가져오는 헬퍼 함수
def _get_food_info(oheng_name: str) -> str:
    food_str = OHENG_ATTRIBUTES.get(oheng_name, {}).get('food', '')
    if not food_str: return "추천 음식"
    # 앞의 2개만 추출하여 '·'로 연결
    foods = [f.strip() for f in food_str.split(',') if f.strip()][:2]
    return '·'.join(foods)

# 리스트를 "A와 B" 형태로 변환하는 헬퍼
def _format_list(items: List[str]) -> str:
    if not items: return ""
    if len(items) == 1: return items[0]
    return f"{', '.join(items[:-1])}와 {items[-1]}"


def define_oheng_messages(
    lacking: List[str], 
    strong: List[str], 
    oheng_type: str,
    final_oheng_scores: Dict[str, float]
) -> Tuple[str, str, Dict[str, int], List[str], List[str]]:
    """
    사용자의 오행 비율을 바탕으로 맞춤형 제목과 조언 메시지를 생성한다. 

    - 과다한(Strong) 오행: 상극(Counter) 오행의 음식을 추천해 기운을 억제한다.
    - 부족한(Lacking) 오행: 해당 오행의 속성을 직접 추천하여 기운을 보충한다.
    """
    # --- 1. 제목 생성 ---
    lacking_str = _format_list(lacking)
    strong_str = _format_list(strong)
    
    if oheng_type == "균형형":
        t1 = lacking[0] if lacking else ""
        t2 = strong[0] if strong else ""
        
        if t1 and t2:
            # 각 오행이 평균(20%)에서 얼마나 떨어져 있는지 계산
            gap_lacking = abs(final_oheng_scores.get(t1, 0) - 20)
            gap_strong = abs(final_oheng_scores.get(t2, 0) - 20)
            
            # 편차가 더 큰 쪽(더 치우친 쪽)을 선택
            target_name = t1 if gap_lacking >= gap_strong else t2
        else:
            target_name = t1 or t2
            
        state = "가장 약해요" if target_name in lacking else "살짝 강해요"
        headline = f"오행이 안정된 하루, {target_name} 기운이 {state}."
    else:
        if lacking and strong:
            headline = f"{strong_str} 기운이 강하고, {lacking_str} 기운이 부족한 하루예요!"
        elif lacking:
            headline = f"{lacking_str} 기운이 부족한 하루예요!"
        else:
            headline = f"{strong_str} 기운이 강한 하루예요!"

    # --- 2. 조언 생성 ---
    advice_parts = []
    recom_counter = Counter()
    closing = random.choice(CLOSING_MESSAGES)

    # 강한 오행을 제어할 상극(Counter) 오행 리스트 생성
    control_ohengs = [
        COUNTER_OHENG_MAP.get(s, "") 
        for s in strong 
        if s in COUNTER_OHENG_MAP
    ]    

    # A. 특수 케이스: 부족 오행과 제어 오행이 겹치는 경우 (치우침/무형)
    is_overlapped = len(lacking) == 1 and len(strong) == 1 and lacking[0] == control_ohengs[0]
    
    if is_overlapped:
        name = lacking[0]
        food = _get_food_info(name)
        advice_parts.append(f"오늘은 {food}을 추천해요! {food}으로 강한 {strong[0]} 기운을 누르고 부족한 {name} 기운을 채우면 {closing}")
        recom_counter[name] += 1
        
    # B. 일반 케이스 (균형형 포함)
    else:
        # 부족 오행 보충
        if lacking:
            l_str = _format_list(lacking)
            if len(lacking) == 1:
                advice_parts.append(f"부족한 {l_str} 기운은 {_get_food_info(lacking[0])}으로 보충하면 좋아요!")
            else:
                advice_parts.append(f"부족한 {l_str} 기운을 보충하면 좋아요!")
            for l in lacking: recom_counter[l] += 1
            
        # 강한 오행 억제
        if strong:
            s_str = _format_list(strong)
            c_str = _format_list(control_ohengs)
            if len(strong) == 1:
                advice_parts.append(f"강한 {s_str} 기운은 상극인 {c_str} 기운을 더해주는 {_get_food_info(control_ohengs[0])}으로 눌러 균형을 맞추면 {closing}")
            else:
                advice_parts.append(f"강한 {s_str} 기운은 상극인 {c_str} 기운으로 눌러 균형을 맞추면 {closing}")
            for c in control_ohengs: recom_counter[c] += 1

    return headline, " ".join(advice_parts), dict(recom_counter), control_ohengs, strong


async def get_today_saju_analysis(uid: str, db: Session) -> Dict:
    """
    오늘의 일진을 반영한 오행 비율, 과다 오행, 부족 오행, 제어 오행, 조언 메시지를 반환합니다.
    """
    # 1. 사용자 조회
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise NotFoundException(resource="사용자")

    # 2. 오늘의 일진을 반영한 오행 점수 계산
    oheng_scores = await calculate_today_saju_iljin(user, db)
    
    # 3. 오행 유형 분류 및 과다/부족 오행 추출
    analysis = classify_and_determine_recommendation(oheng_scores)
    
    # 4. 추천 메시지 및 제어 오행 추출
    headline, advice, recom_weights, control_ohengs, strong_ohengs = define_oheng_messages(
        lacking=analysis["primary_supplement_oheng"],
        strong=analysis["secondary_control_oheng"],
        oheng_type=analysis["oheng_type"],
        final_oheng_scores=oheng_scores
    )
    
    # 필요한 모든 데이터를 딕셔너리로 패키징
    return {
        "lacking_oheng": analysis["primary_supplement_oheng"],
        "strong_ohengs": strong_ohengs,
        "control_ohengs": control_ohengs,
        "oheng_type": analysis["oheng_type"],
        "oheng_scores": oheng_scores,
        "headline": headline,
        "advice": advice,
        "recom_weights": recom_weights
    }