from sqlalchemy.orm import Session
from datetime import date, time, timedelta, datetime
from typing import Optional, Dict
from sqlalchemy import desc
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from core.models import User, Manse 
from saju.saju_calculator import get_time_pillar, calculate_oheng_score
from saju.saju_data import get_ten_star, get_five_circle_from_char

# 한글 오행 키 리스트
OHENG_KOREAN_KEYS = ["목(木)", "화(火)", "토(土)", "금(金)", "수(水)"]

# DB 필드와 매핑이 필요한 경우
KOREAN_TO_DB_MAP = {
    "목(木)": "oheng_wood",
    "화(火)": "oheng_fire",
    "토(土)": "oheng_earth",
    "금(金)": "oheng_metal",
    "수(水)": "oheng_water"
}


# Manse 테이블에서 자시, 절입 시간 보정
def _get_manse_record(
    db: Session, 
    birth_date: date, 
    birth_time: Optional[time], 
    birth_calendar: str
) -> Optional[Manse]:
    
    # 1. 자시(子時) 처리: 23:30 이후 출생이면 사주상 다음 날의 일주로 간주 (일주 보정)
    search_date = birth_date
    if birth_time and birth_time >= time(23, 30):
        search_date += timedelta(days=1)
    
    # 2. 만세력 레코드 조회
    if birth_calendar == "solar":
        manse_record = db.query(Manse).filter(Manse.solarDate == search_date).first()
    
    # 음력/윤달 분기 처리
    elif birth_calendar.startswith("lunar"):
        
        # 'lunar_leap'일 경우 is_leap_month = 1 (True), 아니면 0 (False)
        is_leap_month = 1 if birth_calendar == "lunar_leap" else 0
        
        manse_record = db.query(Manse).filter(
            Manse.lunarDate == search_date,
            # DB의 leapMonth 필드를 is_leap_month 변수 값으로 필터링
            Manse.leapMonth == is_leap_month 
        ).first()
        
    else:
        return None
    
    if not manse_record:
        return None

    # 3. 절입 시간(seasonStartTime) 처리 (월주/년주 보정)
    if manse_record.seasonStartTime and birth_time:
        
        # 실제 출생 시각
        birth_datetime_user = datetime.combine(birth_date, birth_time)
        season_datetime = manse_record.seasonStartTime
        
        # 실제 출생 시각이 절입 시각보다 빠른 경우 (이전 절기의 월주/년주 사용)
        if birth_datetime_user < season_datetime:
            
            # 현재 레코드의 solarDate보다 작으면서, 가장 최신인 레코드 (직전 절기)를 찾음
            previous_manse_record = db.query(Manse).filter(
                Manse.solarDate < manse_record.solarDate
            ).order_by(desc(Manse.solarDate)).first()
            
            if previous_manse_record:
                # 이전 절기의 월주와 년주를 현재 사주에 적용
                manse_record.yearSky = previous_manse_record.yearSky
                manse_record.yearGround = previous_manse_record.yearGround
                manse_record.monthSky = previous_manse_record.monthSky
                manse_record.monthGround = previous_manse_record.monthGround
            
    return manse_record

# 사주 오행 계산 및 저장
async def calculate_saju_and_save(
    user: User,
    db: Session
) -> Dict[str, float]:
    
    birth_date = user.birth_date
    birth_time = user.birth_time
    birth_calendar = user.birth_calendar
    
    if not all([birth_date, birth_calendar]):
        raise HTTPException(status_code=400, detail="사주 계산에 필요한 생년월일 정보가 부족합니다.")
    
    # 1. 만세력 데이터 조회 및 보정 (삼주 확보)
    #manse_record = _get_manse_record(db, birth_date, birth_time, birth_calendar)
    manse_record = await run_in_threadpool(
        _get_manse_record, 
        db, 
        user.birth_date, 
        user.birth_time, 
        user.birth_calendar
    )
    
    if not manse_record:
        raise HTTPException(status_code=404, detail="만세력 데이터베이스에서 해당 기록을 찾을 수 없어 사주 계산을 완료할 수 없습니다.")
    
    # 2. 시주 계산 (사주팔자 완성)
    time_pillar = get_time_pillar(manse_record.daySky, birth_time)
    
    # 사주팔자 기둥 구성
    saju_pillars = {
        'year_sky': manse_record.yearSky, 'year_ground': manse_record.yearGround,
        'month_sky': manse_record.monthSky, 'month_ground': manse_record.monthGround,
        'day_sky': manse_record.daySky, 'day_ground': manse_record.dayGround,
        'time_sky': time_pillar.get('time_sky') if time_pillar else None, 
        'time_ground': time_pillar.get('time_ground') if time_pillar else None,
    }

    # 3. 오행 비율 계산
    oheng_percentages = calculate_oheng_score(saju_pillars)

    # 4. Users 테이블에 오행 정보 업데이트 및 저장
    user.oheng_wood = oheng_percentages.get("oheng_wood")
    user.oheng_fire = oheng_percentages.get("oheng_fire")
    user.oheng_earth = oheng_percentages.get("oheng_earth")
    user.oheng_metal = oheng_percentages.get("oheng_metal")
    user.oheng_water = oheng_percentages.get("oheng_water")
    
    user.day_sky = saju_pillars['day_sky']  # 사용자 사주 일간 필드 추가
    
    #db.commit()
    #db.refresh(user)
    await run_in_threadpool(db.commit)
    await run_in_threadpool(db.refresh, user)
    
    return oheng_percentages

# 사용자의 일주 계산
def _get_user_day_pillar(db: Session, user: User) -> Dict:
    birth_date = user.birth_date
    birth_time = user.birth_time
    birth_calendar = user.birth_calendar
    
    if not all([birth_date, birth_calendar]):
        raise HTTPException(status_code=400, detail="일간 복구에 필요한 생년월일 정보가 부족합니다.")
    
    # 1. 만세력 데이터 조회 및 보정 (자시 보정 포함)
    manse_record = _get_manse_record(db, birth_date, birth_time, birth_calendar)
    
    if not manse_record:
        raise HTTPException(status_code=404, detail="만세력 데이터베이스에서 해당 기록을 찾을 수 없어 일간 복구를 완료할 수 없습니다.")
    
    # 일주 반환
    return {
        'day_sky': manse_record.daySky, 
        'day_ground': manse_record.dayGround
    }

# 오늘의 일진에 따라 사주 오행 비율 보정
async def calculate_today_saju_iljin(
    user: User,
    db: Session
) -> Dict: 
    user_day_sky = user.day_sky
    
    # Users 테이블에 day_sky만 없는 경우
    if not user_day_sky: 
        try:
            #day_pillar = _get_user_day_pillar(db, user) 
            day_pillar = await run_in_threadpool(_get_user_day_pillar, db, user) 
            user.day_sky = day_pillar['day_sky']
            await run_in_threadpool(db.commit)
            await run_in_threadpool(db.refresh, user)
            user_day_sky = user.day_sky 
        except HTTPException:
            raise 
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="기존 유저 일간 데이터 불러오는 중 오류 발생")
    
    # 사용자의 오행 값이 없는 경우 생년월일시에 따라 오행 계산 후 저장
    oheng_fields = ["oheng_wood", "oheng_fire", "oheng_earth", "oheng_metal", "oheng_water"]
    if all(getattr(user, f) is None for f in oheng_fields):
        try:
            await calculate_saju_and_save(user, db)
            await run_in_threadpool(db.refresh, user)
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"사용자 오행 데이터 계산 실패: {e}")

    # 1: 일진 - 오늘의 간지 데이터 가져오기
    today_date = date.today()
    #today_manse = db.query(Manse).filter(Manse.solarDate == today_date).first() 
    today_manse = await run_in_threadpool(
        lambda: db.query(Manse).filter(Manse.solarDate == today_date).first()
    )
    
    if not today_manse or not user_day_sky:
        raise HTTPException(status_code=404, detail="계산에 필요한 유저 정보 혹은 일진 데이터가 부족함")

    today_day_sky = today_manse.daySky      # 오늘의 일간
    today_day_ground = today_manse.dayGround # 오늘의 일지
    
    # 2. 십신 계산
    try:
        ten_star_map = get_ten_star() 
        ten_star_data = ten_star_map.get(user_day_sky, {}).get(today_day_sky)
        
        main_ten_star = ten_star_data[0] if ten_star_data else "데이터 매핑 오류"
            
    except Exception:
        main_ten_star = "십신 계산 오류"

    # 오늘의 일진 오행 키를 "목(木)" 형태로 변환
    def get_korean_hanja_oheng(oheng_korean: str) -> str:
        mapping = {
            "목": "목(木)", "화": "화(火)", "토": "토(土)", "금": "금(金)", "수": "수(水)"
        }
        if not oheng_korean:
            return ""
        oheng_korean = oheng_korean.strip()
        
        # 이미 '목(木)' 형태라면 그대로 반환
        if "(" in oheng_korean: 
            return oheng_korean
        
        # '목'만 있다면 '목(木)'으로 변환
        return mapping.get(oheng_korean, oheng_korean)


    # 3: 오행 비율 보정 
    
    # DB 로드 시 float()으로 강제 형변환 및 None/잘못된 값 처리
    def get_user_oheng_value(value):
        try:
            return float(value) if value is not None else 0.0
        except (ValueError, TypeError):
            # 값이 float으로 변환 불가능한 경우 0.0 처리
            return 0.0

    oheng_scores = {
        "목(木)": get_user_oheng_value(user.oheng_wood), 
        "화(火)": get_user_oheng_value(user.oheng_fire), 
        "토(土)": get_user_oheng_value(user.oheng_earth), 
        "금(金)": get_user_oheng_value(user.oheng_metal), 
        "수(水)": get_user_oheng_value(user.oheng_water), 
    }
    
    # 일진 보정 전 생년월일시에 따라 결정된 사용자 오행 비율
    print(f"보정 전 사용자 오행 비율 (총합 {sum(oheng_scores.values()):.2f}): {oheng_scores}")
    
    today_scores = oheng_scores.copy()
    
    WEIGHT_SKY = 20.0
    WEIGHT_GROUND = 20.0 

    # 오늘의 일진 오행 변환 및 가중치 부여
    today_sky_oheng_raw = get_five_circle_from_char(today_day_sky)
    today_ground_oheng_raw = get_five_circle_from_char(today_day_ground)

    # 키 통일 및 가중치 추가
    today_sky_oheng = None
    today_ground_oheng = None

    if today_sky_oheng_raw:
        today_sky_oheng = get_korean_hanja_oheng(today_sky_oheng_raw)
        if today_sky_oheng in today_scores:
            today_scores[today_sky_oheng] += WEIGHT_SKY
        else:
            print(f"일간 키 불일치. today_sky_oheng: '{today_sky_oheng}'")
    
    if today_ground_oheng_raw:
        today_ground_oheng = get_korean_hanja_oheng(today_ground_oheng_raw)
        if today_ground_oheng in today_scores:
            today_scores[today_ground_oheng] += WEIGHT_GROUND
        else:
            print(f"일지 키 불일치. today_ground_oheng: '{today_ground_oheng}'")
        
    # 가중치 적용 후 오행 비율 출력
    print(f"가중치 적용 후 오행 비율 (총합 {sum(today_scores.values()):.2f}): {today_scores}")

    # 100% 재정규화
    total_sum = sum(today_scores.values()) 
    if total_sum == 0:
        today_oheng_percentages = {k: 0.0 for k in today_scores.keys()}
    else:
        today_oheng_percentages = {k: round((v / total_sum) * 100, 2) for k, v in today_scores.items()}
    
    # 100% 재정규화 후 최종 오행 비율 출력
    print(f"최종 보정된 오행 비율 (총합 {sum(today_oheng_percentages.values()):.2f}): {today_oheng_percentages}")
    
    # 최종 결과 반환
    return {
        "today_iljin_pillars": {"day_sky": today_day_sky, "day_ground": today_day_ground},
        "main_ten_star": main_ten_star,
        "today_oheng_percentages": {
            "ohengWood": today_oheng_percentages.get("목(木)", 0.0),
            "ohengFire": today_oheng_percentages.get("화(火)", 0.0),
            "ohengEarth": today_oheng_percentages.get("토(土)", 0.0),
            "ohengMetal": today_oheng_percentages.get("금(金)", 0.0),
            "ohengWater": today_oheng_percentages.get("수(水)", 0.0),
        },
        "user_day_sky": user_day_sky
    }
    
async def recalculate_and_update_saju(user: User, db: Session):
    """
    사용자의 생년월일/시/음양력 정보가 변경되었을 때 사주를 재계산하고 User 모델을 업데이트합니다.
    """
    if not user.birth_date:
        # 생년월일이 없으면 사주 계산 불가
        print(f"User {user.id} has no birth date, skipping saju calculation.")
        return

    # 1. 만세력 기록 조회 (일주 보정 및 절기 기준)
    manse_record = await run_in_threadpool(
        _get_manse_record, 
        db, 
        user.birth_date, 
        user.birth_time, 
        user.birth_calendar
    )

    if not manse_record:
        # 사주 계산에 필요한 만세력 데이터가 없는 경우
        print(f"ERROR: No Manse record found for user {user.id} on date {user.birth_date}")
        user.day_sky = None
        user.oheng_wood = user.oheng_fire = user.oheng_earth = user.oheng_metal = user.oheng_water = None
        return

    # 2. 시주 계산 (생시가 None이면 시주는 None)
    # get_time_pillar 함수가 'time_sky', 'time_ground'를 반환한다고 가정
    time_pillar_data = get_time_pillar(manse_record.daySky, user.birth_time)
    
    # 3. 사주 기둥 데이터 준비 (calculate_oheng_score 함수가 딕셔너리를 입력받는다고 가정)
    saju_pillars_data = {
        'year_sky': manse_record.yearSky,
        'year_ground': manse_record.yearGround,
        'month_sky': manse_record.monthSky,
        'month_ground': manse_record.monthGround,
        'day_sky': manse_record.daySky,
        'day_ground': manse_record.dayGround,
        'time_sky': time_pillar_data.get('time_sky'),
        'time_ground': time_pillar_data.get('time_ground')
    }

    # 4. 오행 점수 계산 (calculate_oheng_score는 동기 함수로 가정)
    oheng_scores_raw = await run_in_threadpool(calculate_oheng_score, saju_pillars_data)

    # 5. User 모델 필드 업데이트
    # DB에 저장된 오행 점수 필드 (models.py 참고) 업데이트
    user.day_sky = manse_record.daySky # 일간은 사주 핵심 정보이므로 저장
    user.oheng_wood = oheng_scores_raw.get('목(木)')
    user.oheng_fire = oheng_scores_raw.get('화(火)')
    user.oheng_earth = oheng_scores_raw.get('토(土)')
    user.oheng_metal = oheng_scores_raw.get('금(金)')
    user.oheng_water = oheng_scores_raw.get('수(水)')
    
    # (선택) 오행 분석 결과를 저장하는 필드가 있다면 추가 업데이트 가능
    # analysis_result = classify_and_determine_recommendation(oheng_scores_raw)
    # user.oheng_type = analysis_result['oheng_type']