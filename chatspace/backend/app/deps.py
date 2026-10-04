from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User
from .security import decode_token

bearer = HTTPBearer(auto_error=False)


async def get_session(request: Request):
    async with request.app.state.sessionmaker() as session:
        yield session


async def current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    s: AsyncSession = Depends(get_session),
) -> User:
    user_id = decode_token(creds.credentials, request.app.state.settings.jwt_secret) if creds else None
    user = await s.get(User, user_id) if user_id else None
    if user is None:
        raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    return user


async def rate_limit_auth(request: Request):
    st = request.app.state
    key = f"ratelimit:auth:{request.client.host if request.client else 'unknown'}"
    n = await st.redis.incr(key)
    if n == 1:
        await st.redis.expire(key, 60)
    if n > st.settings.auth_rate_limit:
        raise HTTPException(429, "Too many requests")
