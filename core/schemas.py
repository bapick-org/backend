from pydantic import BaseModel, EmailStr, Field

# --- 공통 응답 관련 ---
class ErrorResponse(BaseModel):
    code: str
    message: str
    

# --- 사용자 관련 ---
class UserBase(BaseModel):
    nickname: str = Field(..., min_length=2, max_length=10) 
    gender: str
    birthCalendar: str
    birthDate: str
    birthHour: str
    birthMinute: str
    timeUnknown: bool

# 회원가입 요청 시 (이메일 포함)
class RegisterRequest(UserBase):
    email: EmailStr

# 게스트 요청 시 (이메일 미포함)
class GuestRegisterRequest(UserBase):
    pass

# 성공 응답 규격
class UserResponse(BaseModel):
    firebase_uid: str
    nickname: str

    class Config:
        from_attributes = True