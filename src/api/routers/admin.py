from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from src.db import async_session

router = APIRouter(prefix="/admin", tags=["admin"])


class UserCreateRequest(BaseModel):
    user_id: str
    display_name: str
    role: str = "employee"
    team: Optional[str] = None
    vip_flag: bool = False
    preferences: dict = {}


class UserUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    team: Optional[str] = None
    vip_flag: Optional[bool] = None
    preferences: Optional[dict] = None


class ConfigCreateRequest(BaseModel):
    key: str
    value: str
    description: Optional[str] = None
    is_sensitive: bool = False


class ConfigUpdateRequest(BaseModel):
    value: Optional[str] = None
    description: Optional[str] = None
    is_sensitive: Optional[bool] = None


@router.post("/users")
async def create_user(request: UserCreateRequest):
    from src.db.models import UserProfile

    async with async_session() as session:
        result = await session.execute(select(UserProfile).where(UserProfile.user_id == request.user_id))
        existing = result.scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail="User already exists")

        user = UserProfile(
            user_id=request.user_id,
            display_name=request.display_name,
            role=request.role,
            team=request.team,
            vip_flag=request.vip_flag,
            preferences=request.preferences,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return {"status": "created", "user_id": user.user_id}


@router.get("/users")
async def list_users(role: str = None, team: str = None, vip: bool = None, limit: int = 50):
    from src.db.models import UserProfile

    async with async_session() as session:
        query = select(UserProfile).limit(limit)
        if role:
            query = query.where(UserProfile.role == role)
        if team:
            query = query.where(UserProfile.team == team)
        if vip is not None:
            query = query.where(UserProfile.vip_flag == vip)

        result = await session.execute(query)
        users = result.scalars().all()
        return {
            "users": [
                {
                    "user_id": u.user_id,
                    "display_name": u.display_name,
                    "role": u.role,
                    "team": u.team,
                    "vip_flag": u.vip_flag,
                }
                for u in users
            ],
            "total": len(users),
        }


@router.get("/users/{user_id}")
async def get_user(user_id: str):
    from src.db.models import UserProfile

    async with async_session() as session:
        result = await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return {
            "user_id": user.user_id,
            "display_name": user.display_name,
            "role": user.role,
            "team": user.team,
            "vip_flag": user.vip_flag,
            "preferences": user.preferences,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        }


@router.put("/users/{user_id}")
async def update_user(user_id: str, request: UserUpdateRequest):
    from src.db.models import UserProfile

    async with async_session() as session:
        result = await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if request.display_name is not None:
            user.display_name = request.display_name
        if request.role is not None:
            user.role = request.role
        if request.team is not None:
            user.team = request.team
        if request.vip_flag is not None:
            user.vip_flag = request.vip_flag
        if request.preferences is not None:
            user.preferences = request.preferences

        await session.commit()
        await session.refresh(user)
        return {"status": "updated", "user_id": user.user_id}


@router.delete("/users/{user_id}")
async def delete_user(user_id: str):
    from src.db.models import UserProfile

    async with async_session() as session:
        result = await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        await session.delete(user)
        await session.commit()
        return {"status": "deleted", "user_id": user_id}


@router.post("/config")
async def create_config(request: ConfigCreateRequest):
    from src.db.models import Config

    async with async_session() as session:
        result = await session.execute(select(Config).where(Config.key == request.key))
        existing = result.scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail="Config key already exists")

        config = Config(
            key=request.key,
            value=request.value,
            description=request.description,
            is_sensitive=request.is_sensitive,
        )
        session.add(config)
        await session.commit()
        await session.refresh(config)
        return {"status": "created", "key": config.key}


@router.get("/config")
async def list_configs(category: str = None, limit: int = 50):
    from src.db.models import Config

    async with async_session() as session:
        query = select(Config).limit(limit)
        if category:
            query = query.where(Config.category == category)

        result = await session.execute(query)
        configs = result.scalars().all()
        return {
            "configs": [
                {
                    "key": c.key,
                    "value": "***" if c.is_sensitive else c.value,
                    "description": c.description,
                    "category": c.category,
                    "is_sensitive": c.is_sensitive,
                }
                for c in configs
            ],
            "total": len(configs),
        }


@router.get("/config/{key}")
async def get_config(key: str):
    from src.db.models import Config

    async with async_session() as session:
        result = await session.execute(select(Config).where(Config.key == key))
        config = result.scalar_one_or_none()
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")
        return {
            "key": config.key,
            "value": "***" if config.is_sensitive else config.value,
            "description": config.description,
            "category": config.category,
            "is_sensitive": config.is_sensitive,
        }


@router.put("/config/{key}")
async def update_config(key: str, request: ConfigUpdateRequest):
    from src.db.models import Config

    async with async_session() as session:
        result = await session.execute(select(Config).where(Config.key == key))
        config = result.scalar_one_or_none()
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")

        if request.value is not None:
            config.value = request.value
        if request.description is not None:
            config.description = request.description
        if request.is_sensitive is not None:
            config.is_sensitive = request.is_sensitive

        await session.commit()
        await session.refresh(config)
        return {"status": "updated", "key": config.key}


@router.delete("/config/{key}")
async def delete_config(key: str):
    from src.db.models import Config

    async with async_session() as session:
        result = await session.execute(select(Config).where(Config.key == key))
        config = result.scalar_one_or_none()
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")

        await session.delete(config)
        await session.commit()
        return {"status": "deleted", "key": key}
