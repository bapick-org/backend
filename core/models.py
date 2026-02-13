from typing import List
from datetime import datetime
from sqlalchemy.orm import relationship
from sqlalchemy import Column, Integer, String, Date, Time, DateTime, Boolean, Float, Text, ForeignKey, Enum, DECIMAL, UniqueConstraint
from core.db import Base

class User(Base):
    __tablename__ = "Users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    firebase_uid = Column(String(128), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    nickname = Column(String(50))
    gender = Column(String(10), nullable=False)
    birth_date = Column(Date, nullable=False)
    birth_time = Column(Time, nullable=True)
    birth_calendar = Column(String(20), nullable=False, default="solar")
    profile_image = Column(String(255), nullable=True)
    oheng_wood = Column(Float, nullable=True)
    oheng_fire = Column(Float, nullable=True)
    oheng_earth = Column(Float, nullable=True)
    oheng_metal = Column(Float, nullable=True)
    oheng_water = Column(Float, nullable=True)
    day_sky = Column(String(10), nullable=True)
    
    scraps = relationship("Scrap", back_populates="user")
    collections = relationship("Collection", back_populates="user")
    reservations = relationship("Reservation", back_populates="user")
    chatroom_memberships = relationship("ChatroomMember", back_populates="user")
    
    # Friendships 관계 추가
    # 1. 내가 보낸 친구 요청 목록
    sent_friend_requests = relationship(
        "Friendships", 
        foreign_keys='[Friendships.requester_id]', 
        back_populates="requester"
    )
    # 2. 내가 받은 친구 요청 목록
    received_friend_requests = relationship(
        "Friendships", 
        foreign_keys='[Friendships.receiver_id]', 
        back_populates="receiver"
    )
    
class ChatRoom(Base):
    __tablename__ = "Chat_rooms"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(30), nullable=True)     
    is_group = Column(Boolean, nullable=False, default=False)    
    last_message_id = Column(Integer, nullable=True) 
    selected_menu = Column(String(255), nullable=True)
    
    memberships = relationship("ChatroomMember", cascade="all, delete-orphan")
    messages = relationship("ChatMessage", back_populates="chatroom", passive_deletes=True)
    latest_message = relationship(
        "ChatMessage", 
        primaryjoin="ChatRoom.last_message_id == ChatMessage.id",
        foreign_keys=[last_message_id],
        uselist=False,
    )

class ChatMessage(Base):
    __tablename__ = "Chat_messages"
    id = Column(Integer, primary_key=True)
    room_id = Column(Integer, ForeignKey("Chat_rooms.id", ondelete="CASCADE"), index=True, nullable=False)
    sender_id = Column(String)
    role = Column(String)
    content = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)
    message_type = Column(String(50), default="text")

    chatroom = relationship("ChatRoom", back_populates="messages")

class ChatroomMember(Base):
    __tablename__ = "Chatroom_members"

    user_id = Column(Integer, ForeignKey('Users.id'), primary_key=True)
    chatroom_id = Column(Integer, ForeignKey("Chat_rooms.id", ondelete="CASCADE"), primary_key=True)
    role = Column(String(20), nullable=False)
    joined_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", back_populates="chatroom_memberships")

    def __repr__(self):
        return f"<ChatroomMember(user_id={self.user_id}, chatroom_id={self.chatroom_id}, role='{self.role}')>"
    
class Manse(Base):
    __tablename__ = "manses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    solarDate = Column(Date, nullable=False)
    lunarDate = Column(Date, nullable=False)
    season = Column(String(10))
    seasonStartTime = Column(DateTime, default=None)
    leapMonth = Column(Boolean)
    yearSky = Column(String(10))
    yearGround = Column(String(10))
    monthSky = Column(String(10))
    monthGround = Column(String(10))
    daySky = Column(String(10))
    dayGround = Column(String(10))

class Restaurant(Base):
    __tablename__ = "Restaurants"
    
    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    name = Column(String(100), nullable=False)
    category = Column(String(50), nullable=False)
    address = Column(String(200), nullable=False)
    phone = Column(String(20), nullable=True)
    image = Column(String(2000), nullable=True) 
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    
    menus = relationship("Menu", back_populates="restaurant")
    hours = relationship("OpeningHour", back_populates="restaurant")
    facility_associations = relationship("RestaurantFacility", back_populates="restaurant")
    reviews = relationship("Reviews", back_populates="restaurant")
    scraps = relationship("Scrap", back_populates="restaurant")
    reservations = relationship("Reservation", back_populates="restaurant")
    
    @property
    def facilities(self) -> List["Facility"]:
        return [assoc.facility for assoc in self.facility_associations]

    def __repr__(self):
        return f"<Restaurant(id={self.id}, name='{self.name}', category='{self.category}')>"

class Menu(Base):
    __tablename__ = "Menus"
    
    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    menu_name = Column(String(100), nullable=True)
    menu_price = Column(Integer, nullable=True)
    restaurant_id = Column(Integer, ForeignKey('Restaurants.id'), nullable=False)
    
    restaurant = relationship("Restaurant", back_populates="menus")
    
    def __repr__(self):
        return f"<Menu(id={self.id}, menu_name='{self.menu_name}', restaurant_id={self.restaurant_id})>"
    
class OpeningHour(Base):
    __tablename__ = "OpeningHours"
    
    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    day = Column(String(10), nullable=True)
    open_time = Column(Time, nullable=True)
    close_time = Column(Time, nullable=True)
    break_start = Column(Time, nullable=True)
    break_end = Column(Time, nullable=True)
    last_order = Column(Time, nullable=True)
    is_closed = Column(Boolean, default=False)
    restaurant_id = Column(Integer, ForeignKey('Restaurants.id'), nullable=False)
    
    restaurant = relationship("Restaurant", back_populates="hours")
    
    def __repr__(self):
        return f"<OpeningHour(id={self.id}, day='{self.day}', restaurant_id={self.restaurant_id})>"

class RestaurantFacility(Base):
    __tablename__ = "RestaurantFacilities"
    
    restaurant_id = Column(Integer, ForeignKey("Restaurants.id"), primary_key=True)
    facility_id = Column(Integer, ForeignKey("Facilities.id"), primary_key=True)

    restaurant = relationship("Restaurant", back_populates="facility_associations")
    facility = relationship("Facility", back_populates="restaurants")

    def __repr__(self):
        return f"<RestaurantFacility(r_id={self.restaurant_id}, f_id={self.facility_id})>"

class Facility(Base):
    __tablename__ = "Facilities"
    
    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    name = Column(String(100), nullable=True, unique=True)
    
    restaurants = relationship("RestaurantFacility", back_populates="facility")

    def __repr__(self):
        return f"<Facility(id={self.id}, name='{self.name}')>"
    
class Reviews(Base):
    __tablename__ = "Reviews"
    
    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    rating = Column(DECIMAL(3, 1), nullable=True) 
    visitor_reviews = Column(Integer, nullable=True, default=0)
    blog_reviews = Column(Integer, nullable=True, default=0)
    
    restaurant_id = Column(Integer, ForeignKey('Restaurants.id'), nullable=False)
    
    restaurant = relationship("Restaurant", back_populates="reviews")
    
    def __repr__(self):
        return f"<Reviews(id={self.id}, rating={self.rating}, restaurant_id={self.restaurant_id})>"
    
class Friendships(Base):
    __tablename__ = "Friendships"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    requester_id = Column(Integer, ForeignKey('Users.id'), nullable=False)
    receiver_id = Column(Integer, ForeignKey('Users.id'), nullable=False)
    status = Column(
        Enum('pending', 'accepted', 'rejected', name='friendship_status'), 
        nullable=False, 
        default='pending'
    )
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    __table_args__ = (
        UniqueConstraint('requester_id', 'receiver_id', name='uq_friendship_pair'),
    )
    
    requester = relationship("User", foreign_keys=[requester_id], back_populates="sent_friend_requests")
    receiver = relationship("User", foreign_keys=[receiver_id], back_populates="received_friend_requests")
    
class Collection(Base):
    __tablename__ = "Collections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('Users.id'), nullable=False)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # 관계 설정
    user = relationship("User", back_populates="collections")
    scraps = relationship("Scrap", back_populates="collection")

    def __repr__(self):
        return f"<Collection(id={self.id}, name={self.name})>"
    
class Scrap(Base):
    __tablename__ = "Scraps"

    user_id = Column(Integer, ForeignKey('Users.id'), primary_key=True, nullable=False)
    restaurant_id = Column(Integer, ForeignKey('Restaurants.id'), primary_key=True, nullable=False)
    collection_id = Column(Integer, ForeignKey('Collections.id'), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", back_populates="scraps")
    restaurant = relationship("Restaurant", back_populates="scraps")
    collection = relationship("Collection", back_populates="scraps")

    def __repr__(self):
        return f"<Scrap(user_id={self.user_id}, restaurant_id={self.restaurant_id}, collection_id={self.collection_id})>"

class Reservation(Base):
    __tablename__ = 'Reservations'

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey('Restaurants.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('Users.id'), nullable=False, index=True) 
    reservation_date = Column(Date, nullable=False)
    reservation_time = Column(Time, nullable=False)
    people_count = Column(Integer, nullable=False) 
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    restaurant = relationship("Restaurant", back_populates="reservations")
    user = relationship("User", back_populates="reservations") 

    def __repr__(self):
        return f"<Reservation(id={self.id}, user_id={self.user_id}, restaurant_id={self.restaurant_id})>"