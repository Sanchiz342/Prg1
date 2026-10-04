from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import schemas
from ..db import models as m
from ..db import repositories as repo
from ..errors import Forbidden
from ..services import auth
from .deps import bearer_token, current_user, get_session

router = APIRouter(prefix="/api", tags=["auth"])


@router.get("/auth/status")
def auth_status(s: Session = Depends(get_session)):
    """Public: lets the UI decide between 'create the first admin' and 'log in'."""
    return {"registration_open": repo.count_users(s) == 0}


@router.post("/auth/register", response_model=schemas.TokenOut, status_code=201)
def register(body: schemas.Credentials, s: Session = Depends(get_session)):
    """Only works while no account exists; the first account is the admin."""
    auth.register_first_user(s, body.username, body.password)
    token, user = auth.login(s, body.username, body.password)
    return schemas.TokenOut(token=token, user=schemas.UserOut.model_validate(user))


@router.post("/auth/login", response_model=schemas.TokenOut)
def login(body: schemas.Credentials, s: Session = Depends(get_session)):
    token, user = auth.login(s, body.username, body.password)
    return schemas.TokenOut(token=token, user=schemas.UserOut.model_validate(user))


@router.post("/auth/logout", status_code=204)
def logout(token: str = Depends(bearer_token), s: Session = Depends(get_session), _: m.User = Depends(current_user)):
    auth.logout(s, token)


@router.get("/auth/me", response_model=schemas.UserOut)
def me(user: m.User = Depends(current_user)):
    return user


@router.get("/users", response_model=list[schemas.UserOut])
def list_users(user: m.User = Depends(current_user), s: Session = Depends(get_session)):
    if user.role != "admin":
        raise Forbidden("admin only")
    return repo.list_users(s)


@router.post("/users", response_model=schemas.UserOut, status_code=201)
def create_user(body: schemas.UserCreate, user: m.User = Depends(current_user), s: Session = Depends(get_session)):
    return auth.create_user(s, user, body.username, body.password, body.role)
