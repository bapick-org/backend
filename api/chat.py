import re
import json
import datetime
import pytz
import logging
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel

from core.db import get_db
from core.models import ChatRoom, ChatMessage, ChatroomMember, User
from core.firebase_auth import verify_firebase_token, get_user_uid_from_websocket_token
from core.websocket_manager import ConnectionManager, get_connection_manager

from api.chain import (
    build_conversation_history,
    generate_llm_response,
    get_initial_chat_message,
    search_and_recommend_restaurants,
    get_latest_recommended_foods,
    generate_oheng_explanation,
)

from api.saju import _get_oheng_analysis_data
from saju.message_generator import define_oheng_messages

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)

# KST 시간대 정의 (UTC+9)
KST = pytz.timezone("Asia/Seoul")
UTC = pytz.timezone("UTC")

# 요청 모델
class MessageRequest(BaseModel):
    room_id: int
    message: str


class ChatRoomCreateRequest(BaseModel):
    name: Optional[str] = None
    is_group: bool = False
    invited_uids: Optional[List[str]] = None  # 초대한 사용자 목록


Chat_rooms: Dict[str, list] = {}


# -------------------------------
# 메뉴 / 위치 선택 관련 유틸
# -------------------------------

def get_latest_selected_menu(db: Session, room_id: int) -> Optional[str]:
    """
    ChatRoom에 저장된 가장 최근 선택 메뉴(selected_menu) 조회
    """
    chatroom = db.query(ChatRoom).filter(ChatRoom.id == room_id).first()
    if chatroom:
        return chatroom.selected_menu
    return None


def process_menu_selection(db: Session, chatroom: ChatRoom, llm_output: str) -> Optional[dict]:
    """
    LLM 응답에서 [MENU_SELECTED:xxx] 태그를 찾아서,
    - chatroom.selected_menu에 저장
    - 위치 선택을 위한 location_select 타입 메시지를 하나 생성 & 저장
    """
    menu_name_match = re.search(r"\[MENU_SELECTED:(.+?)\]", llm_output)
    if not menu_name_match:
        return None

    selected_menu = menu_name_match.group(1).strip()

    # ChatRoom에 선택 메뉴 저장
    chatroom.selected_menu = selected_menu
    db.add(chatroom)
    db.commit()

    # 위치 선택 프롬프트 메시지 생성
    assistant_reply = (
        f"그러면 {selected_menu} 먹으러 갈 식당 추천해줄게! 위치는 어디로 할까?\n\n"
        "원하는 위치를 채팅창에 입력하거나 버튼을 눌러줘!"
    )
    message_type = "location_select"

    assistant_message = ChatMessage(
        room_id=chatroom.id,
        sender_id="assistant",
        role="assistant",
        content=assistant_reply,
        message_type=message_type,
        timestamp=datetime.datetime.utcnow(),
    )
    db.add(assistant_message)
    db.commit()
    db.refresh(assistant_message)

    chatroom.last_message_id = assistant_message.id
    db.add(chatroom)
    db.commit()

    return {
        "id": assistant_message.id,
        "role": "assistant",
        "message_type": message_type,
        "content": assistant_reply,
    }


def process_location_selection_tag(
    db: Session,
    chatroom: ChatRoom,
    user_message_content: str,
    user_message_id: int,
) -> Optional[Dict[str, Any]]:
    """
    [LOCATION_SELECTED:TYPE]|lat|lon 태그 처리.
    - ChatRoom.selected_menu로부터 메뉴명 읽고
    - search_and_recommend_restaurants(menu, db, lat, lon) 호출
    - DB에 initial / restaurant_cards / final 메시지 3개 저장
    - 프론트로 보낼 수 있는 reply 구조 반환
    """

    location_selection_regex = re.compile(
        r"\[LOCATION_SELECTED:(SAVED_LOCATION|CURRENT_LOCATION|MANUAL_LOCATION)\]\|(-?\d+\.\d+)\|(-?\d+\.\d+)"
    )
    match = location_selection_regex.match(user_message_content)
    if not match:
        return None

    action_type = match.group(1).strip()
    lat = float(match.group(2))
    lon = float(match.group(3))

    selected_menu = get_latest_selected_menu(db, chatroom.id)

    print(f"[DEBUG] LOCATION_SELECTED 처리: action={action_type}, menu={selected_menu}, lat={lat}, lon={lon}")

    # 식당 검색
    restaurant_data = search_and_recommend_restaurants(selected_menu, db, lat, lon)

    restaurants = restaurant_data.get("restaurants", [])

    # 검색 결과 없음
    if not restaurants:
        no_result_msg = restaurant_data.get(
            "message",
            "조건에 맞는 행운의 맛집을 찾지 못했어. 다른 메뉴나 위치로 다시 시도해볼까?",
        )

        no_result_message = ChatMessage(
            room_id=chatroom.id,
            sender_id="assistant",
            role="assistant",
            content=no_result_msg,
            message_type="text",
            timestamp=datetime.datetime.utcnow(),
        )
        db.add(no_result_message)
        db.commit()
        db.refresh(no_result_message)

        # 상태 초기화
        chatroom.selected_menu = None
        chatroom.last_message_id = no_result_message.id
        db.add(chatroom)
        db.commit()

        return {
            "replies": [
                {
                    "id": no_result_message.id,
                    "role": "assistant",
                    "message_type": "text",
                    "content": no_result_msg,
                }
            ],
            "user_message_id": user_message_id,
        }

    # 검색 결과 있음
    print(f"[DEBUG] 식당 검색 성공: {len(restaurants)}개 발견")

    chatroom.selected_menu = None
    db.add(chatroom)
    db.commit()

    initial_msg_content = restaurant_data.get(
        "initial_message",
        f"그러면 {selected_menu} 먹으러 갈 식당을 추천해줄게! 😋",
    )
    final_msg_content = restaurant_data.get(
        "final_message",
        "다른 행운의 맛집도 추천해줄까?",
    )

    card_data = {
        "restaurants": restaurants,
        "count": restaurant_data.get("count", len(restaurants)),
    }
    card_msg_content = json.dumps(card_data, ensure_ascii=False)

    # 1) initial text
    initial_message = ChatMessage(
        room_id=chatroom.id,
        sender_id="assistant",
        role="assistant",
        content=initial_msg_content,
        message_type="text",
        timestamp=datetime.datetime.utcnow(),
    )
    db.add(initial_message)

    # 2) restaurant_cards
    card_message = ChatMessage(
        room_id=chatroom.id,
        sender_id="assistant",
        role="assistant",
        content=card_msg_content,
        message_type="restaurant_cards",
        timestamp=datetime.datetime.utcnow() + datetime.timedelta(seconds=1),
    )
    db.add(card_message)

    # 3) final text
    final_message = ChatMessage(
        room_id=chatroom.id,
        sender_id="assistant",
        role="assistant",
        content=final_msg_content,
        message_type="text",
        timestamp=datetime.datetime.utcnow() + datetime.timedelta(seconds=2),
    )
    db.add(final_message)

    db.commit()
    db.refresh(initial_message)
    db.refresh(card_message)
    db.refresh(final_message)

    chatroom.last_message_id = final_message.id
    db.add(chatroom)
    db.commit()

    return {
        "replies": [
            {
                "id": initial_message.id,
                "role": "assistant",
                "message_type": "text",
                "content": initial_msg_content,
            },
            {
                "id": card_message.id,
                "role": "assistant",
                "message_type": "restaurant_cards",
                "content": card_msg_content,
            },
            {
                "id": final_message.id,
                "role": "assistant",
                "message_type": "text",
                "content": final_msg_content,
            },
        ],
        "user_message_id": user_message_id,
    }


# -------------------------------
# 공통 유틸
# -------------------------------

def chat_message_to_json(
    msg: ChatMessage,
    sender_name: str,
    current_user_uid: str,
    sender_profile_url: Optional[str] = None,
) -> dict:
    is_me = msg.sender_id == current_user_uid

    return {
        "id": msg.id,
        "room_id": msg.room_id,
        "sender_id": msg.sender_id,
        "sender_name": sender_name,
        "sender_profile_url": sender_profile_url,
        "role": msg.role,
        "content": msg.content,
        "message_type": msg.message_type,
        "timestamp": msg.timestamp.isoformat(),
        "is_me": is_me,
    }


# -------------------------------
# WebSocket용 식당 추천(직접 호출용)
# -------------------------------

async def handle_restaurant_recommendation(
    room_id: int,
    selected_menu: str,
    db: Session,
    manager: ConnectionManager,
    chatroom: ChatRoom,
):
    """
    필요하다면 MENU 선택 후 바로 추천할 때 쓰는 함수.
    지금 구조에서는 LOCATION_SELECTED에서 바로 DB저장 + 브로드캐스트를 하므로,
    현재는 안 써도 됨. (남겨두긴 함)
    """
    restaurant_data = search_and_recommend_restaurants(selected_menu, db)

    initial_msg_content = restaurant_data.get("initial_message")
    initial_message = ChatMessage(
        room_id=room_id,
        sender_id="assistant",
        role="assistant",
        content=initial_msg_content,
        message_type="text",
        timestamp=datetime.datetime.utcnow(),
    )
    db.add(initial_message)
    db.flush()

    await manager.broadcast(
        room_id,
        json.dumps(
            {
                "type": "new_message",
                "message": {
                    "id": initial_message.id,
                    "role": "assistant",
                    "sender_name": "밥풀이",
                    "content": initial_msg_content,
                    "message_type": "text",
                    "timestamp": initial_message.timestamp.isoformat(),
                },
            }
        ),
    )

    card_data = {
        "restaurants": restaurant_data.get("restaurants", []),
        "count": restaurant_data.get("count", 0),
    }
    card_msg_content = json.dumps(card_data, ensure_ascii=False)
    card_message = ChatMessage(
        room_id=room_id,
        sender_id="assistant",
        role="assistant",
        content=card_msg_content,
        message_type="restaurant_cards",
        timestamp=datetime.datetime.utcnow() + datetime.timedelta(seconds=1),
    )
    db.add(card_message)
    db.flush()

    await manager.broadcast(
        room_id,
        json.dumps(
            {
                "type": "new_message",
                "message": {
                    "id": card_message.id,
                    "role": "assistant",
                    "sender_name": "밥풀이",
                    "content": card_msg_content,
                    "message_type": "restaurant_cards",
                    "timestamp": card_message.timestamp.isoformat(),
                },
            }
        ),
    )

    final_msg_content = restaurant_data.get("final_message")
    final_message = ChatMessage(
        room_id=room_id,
        sender_id="assistant",
        role="assistant",
        content=final_msg_content,
        message_type="text",
        timestamp=datetime.datetime.utcnow() + datetime.timedelta(seconds=2),
    )
    db.add(final_message)
    db.commit()
    db.refresh(final_message)

    await manager.broadcast(
        room_id,
        json.dumps(
            {
                "type": "new_message",
                "message": {
                    "id": final_message.id,
                    "role": "assistant",
                    "sender_name": "밥풀이",
                    "content": final_msg_content,
                    "message_type": "text",
                    "timestamp": final_message.timestamp.isoformat(),
                },
            }
        ),
    )

    chatroom.last_message_id = final_message.id
    db.add(chatroom)
    db.commit()


# -------------------------------
# WebSocket 메시지 처리
# -------------------------------

async def handle_websocket_message(
    room_id: int,
    uid: str,
    user: User,
    message_content: str,
    db: Session,
    manager: ConnectionManager,
):
    chatroom = db.query(ChatRoom).filter(ChatRoom.id == room_id).first()
    if not chatroom:
        return

    # 오행 설명 요청 체크
    if message_content == "[REQUEST_OHENG_INFO]":
        # 사용자별 맞춤 메시지 생성
        explanation = await generate_oheng_explanation(uid, db)
        
        info_message = ChatMessage(
            room_id=room_id,
            sender_id="assistant",
            role="assistant",
            content=explanation,
            message_type="oheng_info",
            timestamp=datetime.datetime.utcnow(),
        )
        db.add(info_message)
        db.commit()
        db.refresh(info_message)
        
        # 브로드캐스트
        bot_msg_json = chat_message_to_json(info_message, "밥풀이", uid)
        await manager.broadcast(
            room_id,
            json.dumps({"type": "new_message", "message": bot_msg_json}),
        )
        
        chatroom.last_message_id = info_message.id
        db.add(chatroom)
        db.commit()
        return
        
    # LOCATION_SELECTED 여부 먼저 확인
    is_location_message = message_content.startswith("[LOCATION_SELECTED:")

    # 사용자 메시지 저장
    chat_message = ChatMessage(
        room_id=room_id,
        sender_id=uid,
        role="user",
        content=message_content,
        timestamp=datetime.datetime.utcnow(),
    )
    db.add(chat_message)
    db.commit()
    db.refresh(chat_message)

    sender_profile_url = user.profile_image

    # LOCATION_SELECTED는 프론트에 그대로 보여줄 필요 없으니 브로드캐스트 생략
    if not is_location_message:
        user_msg_json = chat_message_to_json(
            chat_message, user.nickname, uid, sender_profile_url
        )
        await manager.broadcast(
            room_id,
            json.dumps({"type": "new_message", "message": user_msg_json}),
        )

    # 1) LOCATION_SELECTED 처리 (LLM 호출 전에)
    if is_location_message:
        location_result = process_location_selection_tag(
            db, chatroom, message_content, chat_message.id
        )
        if location_result and location_result.get("replies"):
            for reply_msg in location_result["replies"]:
                db_message = (
                    db.query(ChatMessage)
                    .filter(ChatMessage.id == reply_msg["id"])
                    .first()
                )
                if db_message:
                    bot_msg_json = chat_message_to_json(
                        db_message, "밥풀이", uid
                    )
                    await manager.broadcast(
                        room_id,
                        json.dumps(
                            {"type": "new_message", "message": bot_msg_json}
                        ),
                    )
        return

    # 2) 챗봇 호출 여부
    MENTION_TAG = "@밥풀이"
    is_llm_triggered = (not chatroom.is_group) or (
        chatroom.is_group and MENTION_TAG in message_content
    )

    if not is_llm_triggered:
        chatroom.last_message_id = chat_message.id
        db.add(chatroom)
        db.commit()
        return

    # 3) LLM 호출
    try:
        user_message_for_llm = (
            message_content.replace(MENTION_TAG, "").strip()
            if chatroom.is_group
            else message_content
        )

        conversation_history = build_conversation_history(db, room_id)

        print("\n============================")
        print("📩 USER MESSAGE:", user_message_for_llm)
        print("📜 HISTORY:", conversation_history)
        print("============================\n")

        current_foods = get_latest_recommended_foods(db, room_id)

        try:
            # 오행 정보 로딩
            lacking_oheng, strong_oheng_db, oheng_type, oheng_scores = (
                await _get_oheng_analysis_data(uid, db)
            )
            (
                headline,
                advice,
                recommended_ohengs_weights,
                control_ohengs,
                strong_ohengs,
            ) = define_oheng_messages(
                lacking_oheng,
                strong_oheng_db,
                oheng_type,
                oheng_scores
            )

            oheng_info_text = f"""
            부족한 오행: {", ".join(lacking_oheng)}
            강한 오행: {", ".join(strong_ohengs)}
            조절 오행: {", ".join(control_ohengs)}
            """

            llm_output = generate_llm_response(
                conversation_history,
                user_message_for_llm,
                current_recommended_foods=current_foods,
                oheng_info_text=oheng_info_text,
            )

            print("🤖 LLM OUTPUT:", llm_output)

        except Exception as llm_error:
            print("💥 LLM 호출 오류:", llm_error)
            await manager.broadcast(
                room_id,
                json.dumps(
                    {
                        "type": "new_message",
                        "message": {
                            "role": "assistant",
                            "sender_name": "밥풀이",
                            "content": "잠깐 오류났어 😅 다시 한번 말해줄래?",
                            "message_type": "text",
                        },
                    }
                ),
            )
            return

        # 4) LLM 응답에 MENU_SELECTED 태그가 있는 경우 → 위치 선택 단계로
        location_select_reply = process_menu_selection(db, chatroom, llm_output)
        if location_select_reply:
            assistant_message = (
                db.query(ChatMessage)
                .filter(ChatMessage.id == chatroom.last_message_id)
                .first()
            )
            if assistant_message:
                bot_msg_json = chat_message_to_json(
                    assistant_message, "밥풀이", uid
                )
                await manager.broadcast(
                    room_id,
                    json.dumps(
                        {"type": "new_message", "message": bot_msg_json}
                    ),
                )
            return

        # 5) 일반 텍스트 응답
        assistant_message = ChatMessage(
            room_id=room_id,
            sender_id="assistant",
            role="assistant",
            content=llm_output,
            message_type="text",
            timestamp=datetime.datetime.utcnow(),
        )
        db.add(assistant_message)
        db.commit()
        db.refresh(assistant_message)

        bot_msg_json = chat_message_to_json(
            assistant_message, "밥풀이", uid
        )
        await manager.broadcast(
            room_id,
            json.dumps({"type": "new_message", "message": bot_msg_json}),
        )

        chatroom.last_message_id = assistant_message.id
        db.add(chatroom)
        db.commit()

    except Exception as e:
        print("🔥 전체 처리 오류:", e)
        await manager.broadcast(
            room_id,
            json.dumps(
                {
                    "type": "error",
                    "message": "서버에서 오류가 발생했어 😭 다시 시도해줘!",
                }
            ),
        )


# -------------------------------
# WebSocket 엔드포인트
# -------------------------------

@router.websocket("/ws/{room_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    room_id: int,
    token: str,
    db: Session = Depends(get_db),
    manager: ConnectionManager = Depends(get_connection_manager),
):
    try:
        uid = await get_user_uid_from_websocket_token(token)

        user = db.query(User).filter(User.firebase_uid == uid).first()
        if not user:
            await websocket.close(code=1008, reason="등록되지 않은 사용자")
            return

        member = (
            db.query(ChatroomMember)
            .filter(
                ChatroomMember.chatroom_id == room_id,
                ChatroomMember.user_id == user.id,
            )
            .first()
        )
        if not member:
            await websocket.close(code=1008, reason="채팅방 접근 권한 없음")
            return

        await manager.connect(room_id, uid, websocket)

        try:
            while True:
                data = await websocket.receive_text()
                message_data = json.loads(data)

                if message_data.get("type") == "message":
                    await handle_websocket_message(
                        room_id=room_id,
                        uid=uid,
                        user=user,
                        message_content=message_data.get("content"),
                        db=db,
                        manager=manager,
                    )

        except WebSocketDisconnect:
            manager.disconnect(room_id, websocket)
            logger.info(
                f"WebSocket disconnected: Room {room_id}, User {uid}"
            )

    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.close(code=1011, reason=str(e))


# -------------------------------
# 채팅방 생성
# -------------------------------

@router.post("/create")
async def create_chatroom(
    data: ChatRoomCreateRequest,
    uid: str = Depends(verify_firebase_token),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise HTTPException(
            status_code=404, detail="등록되지 않은 사용자입니다."
        )

    all_member_uids = [uid]
    if data.is_group and data.invited_uids:
        for invited_uid in data.invited_uids:
            if invited_uid != uid and invited_uid not in all_member_uids:
                all_member_uids.append(invited_uid)

    members_to_add = (
        db.query(User)
        .filter(User.firebase_uid.in_(all_member_uids))
        .all()
    )

    if data.name:
        final_room_name = data.name
    elif not data.is_group:
        final_room_name = "밥풀이"
    else:
        nicknames = [member.nickname for member in members_to_add]
        if len(nicknames) > 3:
            display_names = ", ".join(nicknames[:3])
            final_room_name = f"{display_names} 외 {len(nicknames) - 3}명"
        else:
            final_room_name = ", ".join(nicknames)

    chatroom = ChatRoom(name=final_room_name, is_group=data.is_group)
    db.add(chatroom)
    db.commit()
    db.refresh(chatroom)

    for member_user in members_to_add:
        role = "owner" if member_user.id == user.id else "member"
        member = ChatroomMember(
            user_id=member_user.id,
            chatroom_id=chatroom.id,
            role=role,
            joined_at=datetime.datetime.utcnow(),
        )
        db.add(member)

    last_message_id = None
    initial_message_content = None

    greeting_message_content = (
        "안녕! 나는 오늘의 운세에 맞춰 행운의 맛집을 추천해주는 '밥풀이'야🍀 지금 너한테 딱 맞는 메뉴 추천해줄까? 먹고 싶은 메뉴 고르면 식당도 알려줄게!"
    )
    greeting_message = ChatMessage(
        room_id=chatroom.id,
        role="assistant",
        content=greeting_message_content,
        sender_id="assistant",
        message_type="greeting",
    )
    db.add(greeting_message)
    db.commit()
        
        
    detailed_message_content = await get_initial_chat_message(uid, db)
    detailed_message = ChatMessage(
        room_id=chatroom.id,
        role="assistant",
        content=detailed_message_content,
        sender_id="assistant",
        message_type="hidden_initial",
    )
    db.add(detailed_message)
    db.commit()


    last_message_id = greeting_message.id
    initial_message_content = greeting_message_content

    chatroom.last_message_id = last_message_id
    db.add(chatroom)
    db.commit()

    room_id_str = str(chatroom.id)
    Chat_rooms[room_id_str] = []

    return {
        "message": "채팅방 생성 완료",
        "chatroom_id": room_id_str,
        "is_group": chatroom.is_group,
        "name": final_room_name,
        "initial_message": initial_message_content,
    }


# -------------------------------
# 채팅방 목록 조회
# -------------------------------

@router.get("/list")
async def list_chatrooms(
    uid: str = Depends(verify_firebase_token),
    is_group: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise HTTPException(
            status_code=404, detail="등록되지 않은 사용자입니다."
        )

    query = (
        db.query(ChatRoom)
        .join(ChatroomMember)
        .filter(ChatroomMember.user_id == user.id)
    )

    if is_group is not None:
        query = query.filter(ChatRoom.is_group == is_group)

    rooms = query.options(joinedload(ChatRoom.latest_message)).all()

    result = []
    for room in rooms:
        latest_msg = room.latest_message
        latest_content = (
            latest_msg.content if latest_msg else "대화 내용 없음"
        )
        latest_timestamp = latest_msg.timestamp if latest_msg else None

        member_count = None
        member_profiles: List[Dict[str, Optional[str]]] = []

        if room.is_group:
            member_count = (
                db.query(ChatroomMember)
                .filter(ChatroomMember.chatroom_id == room.id)
                .count()
            )

            members = (
                db.query(User)
                .join(ChatroomMember)
                .filter(
                    ChatroomMember.chatroom_id == room.id,
                    User.id != user.id,
                )
                .limit(4)
                .all()
            )

            member_profiles = [
                {
                    "nickname": m.nickname,
                    "profile_image": m.profile_image or None,
                }
                for m in members
            ]

        kst_timestamp = None
        if latest_timestamp:
            if latest_timestamp.tzinfo is None:
                utc_dt = UTC.localize(latest_timestamp)
            else:
                utc_dt = latest_timestamp.astimezone(UTC)
            kst_dt = utc_dt.astimezone(KST)
            kst_timestamp = kst_dt.isoformat()

        result.append(
            {
                "id": room.id,
                "name": room.name,
                "is_group": room.is_group,
                "last_message_content": latest_content,
                "last_message_timestamp": kst_timestamp,
                "member_count": member_count,
                "member_profiles": member_profiles,
            }
        )

    return result


# -------------------------------
# 특정 채팅방의 메시지 조회
# -------------------------------

@router.get("/messages/{room_id}")
async def get_messages(
    room_id: int,
    uid: str = Depends(verify_firebase_token),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise HTTPException(
            status_code=404, detail="사용자 인증 실패"
        )

    member = (
        db.query(ChatroomMember)
        .filter(
            ChatroomMember.chatroom_id == room_id,
            ChatroomMember.user_id == user.id,
        )
        .first()
    )
    if not member:
        raise HTTPException(
            status_code=403, detail="이 채팅방에 접근할 권한이 없습니다."
        )

    chatroom = db.query(ChatRoom).filter(ChatRoom.id == room_id).first()

    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.room_id == room_id)
        .order_by(ChatMessage.timestamp)
        .all()
    )

    result = []
    for msg in messages:
        sender_profile_url = None

        if msg.sender_id == "assistant":
            sender_name = "밥풀이"
        else:
            sender = (
                db.query(User)
                .filter(User.firebase_uid == msg.sender_id)
                .first()
            )
            sender_name = (
                sender.nickname if sender and sender.nickname else "알 수 없음"
            )
            sender_profile_url = sender.profile_image if sender else None

        result.append(
            {
                "id": msg.id,
                "user_id": msg.sender_id,
                "role": msg.role,
                "sender_id": msg.sender_id,
                "sender_name": sender_name,
                "sender_profile_url": sender_profile_url,
                "content": msg.content,
                "message_type": msg.message_type,
                "timestamp": msg.timestamp.isoformat()
                if msg.timestamp
                else None,
            }
        )

    return {
        "messages": result,
        "is_group": chatroom.is_group if chatroom else False,
        "chatroom_name": chatroom.name
        if chatroom
        else f"채팅방 #{room_id}",
    }


# -------------------------------
# 채팅방 삭제
# -------------------------------

@router.delete("/{room_id}")
async def delete_chatroom(
    room_id: int,
    uid: str = Depends(verify_firebase_token),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="등록되지 않은 사용자입니다.",
        )

    room = db.query(ChatRoom).filter(ChatRoom.id == room_id).first()
    if not room:
        return {
            "message": "채팅방을 찾을 수 없습니다. 이미 삭제되었을 수 있습니다."
        }

    member = (
        db.query(ChatroomMember)
        .filter(
            ChatroomMember.chatroom_id == room_id,
            ChatroomMember.user_id == user.id,
        )
        .first()
    )
    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="이 채팅방을 삭제할 권한이 없습니다.",
        )

    try:
        db.delete(room)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"채팅방 삭제 중 오류 발생: {e}")

    return {"message": "채팅방 삭제 완료"}


# -------------------------------
# HTTP POST 메시지 전송 (/send)
# -------------------------------

@router.post("/send")
async def send_message(
    request: MessageRequest,
    uid: str = Depends(verify_firebase_token),
    db: Session = Depends(get_db),
    manager: ConnectionManager = Depends(get_connection_manager),
):
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise HTTPException(
            status_code=404, detail="등록되지 않은 사용자입니다."
        )

    chatroom = (
        db.query(ChatRoom)
        .filter(ChatRoom.id == request.room_id)
        .first()
    )
    if not chatroom:
        raise HTTPException(
            status_code=404, detail="채팅방을 찾을 수 없음"
        )

    if request.message == "[REQUEST_OHENG_INFO]":
        # 사용자별 맞춤 메시지 생성
        explanation = await generate_oheng_explanation(uid, db)
        
        info_message = ChatMessage(
            room_id=request.room_id,
            sender_id="assistant",
            role="assistant",
            content=explanation,
            message_type="oheng_info",
            timestamp=datetime.datetime.utcnow(),
        )
        db.add(info_message)
        db.commit()
        db.refresh(info_message)
        
        chatroom.last_message_id = info_message.id
        db.add(chatroom)
        db.commit()
        
        return {
            "reply": {
                "role": "assistant",
                "content": explanation,
                "message_type": "oheng_info",
            },
            "user_message_id": None,
        }
        
    chat_message = ChatMessage(
        room_id=chatroom.id,
        sender_id=uid,
        role="user",
        content=request.message,
        timestamp=datetime.datetime.utcnow(),
    )
    db.add(chat_message)
    db.commit()
    db.refresh(chat_message)

    user_msg_json = chat_message_to_json(
        chat_message, user.nickname, uid
    )
    await manager.broadcast(
        chatroom.id,
        json.dumps({"type": "new_message", "message": user_msg_json}),
    )

    MENTION_TAG = "@밥풀이"
    is_llm_triggered = (not chatroom.is_group) or (
        chatroom.is_group and MENTION_TAG in request.message
    )

    if not is_llm_triggered:
        chatroom.last_message_id = chat_message.id
        db.add(chatroom)
        db.commit()
        return {
            "message": "메시지 전송 완료 (LLM 미호출)",
            "user_message_id": chat_message.id,
        }

    try:
        # 1) LOCATION_SELECTED 먼저 체크
        user_message_content = request.message
        location_select_result = process_location_selection_tag(
            db, chatroom, user_message_content, chat_message.id
        )
        if location_select_result:
            return location_select_result

        # 2) 멘션 태그 제거
        user_message_for_llm = request.message
        if chatroom.is_group:
            user_message_for_llm = request.message.replace(
                MENTION_TAG, ""
            ).strip()

        # 3) 기존 대화 내역 + 오행 + current_foods
        conversation_history = build_conversation_history(
            db, chatroom.id
        )

        print("\n============================")
        print("📩 USER MESSAGE:", user_message_for_llm)
        print("📜 HISTORY:", conversation_history)
        print("============================\n")

        current_foods = get_latest_recommended_foods(db, chatroom.id)

        lacking_oheng, strong_oheng_db, oheng_type, oheng_scores = (
            await _get_oheng_analysis_data(uid, db)
        )
        (
            headline,
            advice,
            recommended_ohengs_weights,
            control_ohengs,
            strong_ohengs,
        ) = define_oheng_messages(
            lacking_oheng, strong_oheng_db, oheng_type, oheng_scores
        )

        oheng_info_text = f"""
        부족한 오행: {", ".join(lacking_oheng)}
        강한 오행: {", ".join(strong_ohengs)}
        조절 오행: {", ".join(control_ohengs)}
        """

        llm_output = generate_llm_response(
            conversation_history,
            user_message_for_llm,
            current_recommended_foods=current_foods,
            oheng_info_text=oheng_info_text,
        )

        # 4) LLM 응답에 MENU_SELECTED → 위치 선택 메시지
        location_select_reply = process_menu_selection(
            db, chatroom, llm_output
        )
        if location_select_reply:
            return {
                "reply": location_select_reply,
                "user_message_id": chat_message.id,
            }

        # 5) 일반 텍스트 응답
        assistant_reply = llm_output
        message_type = "text"

        assistant_message = ChatMessage(
            room_id=chatroom.id,
            sender_id="assistant",
            role="assistant",
            content=assistant_reply,
            message_type=message_type,
            timestamp=datetime.datetime.utcnow(),
        )
        db.add(assistant_message)
        db.commit()
        db.refresh(assistant_message)

        chatroom.last_message_id = assistant_message.id
        db.add(chatroom)
        db.commit()

        return {
            "reply": {
                "role": "assistant",
                "content": assistant_reply,
                "message_type": message_type,
            },
            "user_message_id": chat_message.id,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"LLM 처리 중 오류: {e}"
        )
