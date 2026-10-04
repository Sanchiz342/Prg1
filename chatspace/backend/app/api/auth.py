from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import current_user, get_session, rate_limit_auth
from ..models import User
from ..security import create_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class Register(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_]{3,32}$")
    email: str
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or len(v) > 255:
            raise ValueError("invalid email")
        return v


class Login(BaseModel):
    email: str
    password: str


def user_dict(u: User) -> dict:
    return {"id": u.id, "username": u.username, "email": u.email}


def token_for(request: Request, u: User) -> dict:
    st = request.app.state.settings
    return {"access_token": create_token(u.id, st.jwt_secret, st.jwt_ttl_minutes), "token_type": "bearer", "user": user_dict(u)}


@router.post("/register", status_code=201, dependencies=[Depends(rate_limit_auth)])
async def register(body: Register, request: Request, s: AsyncSession = Depends(get_session)):
    user = User(username=body.username, email=body.email, password_hash=hash_password(body.password))
    s.add(user)
    try:
        await s.commit()
    except IntegrityError:
        await s.rollback()
        raise HTTPException(409, "Username or email already taken")
    return token_for(request, user)


@router.post("/login", dependencies=[Depends(rate_limit_auth)])
async def login(body: Login, request: Request, s: AsyncSession = Depends(get_session)):
    user = await s.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    return token_for(request, user)


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return user_dict(user)
