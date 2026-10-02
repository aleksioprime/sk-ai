"""
Вход через учётные записи Open WebUI.

Отдельных паролей у School AI нет. Когда пользователь входит в Open WebUI,
браузер получает cookie `token`. Cookie не зависят от порта, поэтому та же
cookie приходит и на страницу School AI (порт 8000) на том же адресе.
Мы проверяем её у самого Open WebUI (GET /api/v1/auths/) и получаем
пользователя и его роль.

    смотреть документы, задавать вопросы — роли user и admin
    загружать, перемещать, удалять        — только admin
"""

import time
from dataclasses import dataclass

import httpx
from fastapi import Depends, HTTPException, Request, status

from ..config import settings

_CACHE_SECONDS = 60
_cache: dict[str, tuple[float, "User | None"]] = {}


@dataclass(frozen=True)
class User:
    id: str
    name: str
    email: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get("token")


def _verify(token: str) -> User | None:
    cached = _cache.get(token)
    if cached and time.monotonic() - cached[0] < _CACHE_SECONDS:
        return cached[1]

    user = None
    try:
        response = httpx.get(
            f"{settings.open_webui_url}/api/v1/auths/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            user = User(
                id=str(data.get("id", "")),
                name=data.get("name") or "",
                email=data.get("email") or "",
                role=data.get("role") or "pending",
            )
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Open WebUI недоступен — вход невозможен")

    if len(_cache) > 1000:
        _cache.clear()
    _cache[token] = (time.monotonic(), user)
    return user


def current_user(request: Request) -> User:
    token = _token(request)
    user = _verify(token) if token else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Войдите в Open WebUI")
    if user.role not in ("user", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Учётная запись ещё не одобрена администратором")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Изменять документы может только администратор")
    return user
