"""
image_fetcher.py
Подбор тематических изображений.

Основной источник — Pixabay API (бесплатный, поиск по ключевым словам, обычно
регистрируется проще, чем Pexels/Unsplash — https://pixabay.com/api/docs/).
Если PIXABAY_API_KEY не задан или запрос не удался — используется Picsum Photos
(без ключа, но БЕЗ поиска по смыслу, только детерминированная случайная картинка).

Про Яндекс: у Яндекса нет открытого бесплатного API поиска изображений по ключевым
словам для такого сценария (старый XML-поиск закрыт для новых коммерческих кейсов),
поэтому эта интеграция технически недоступна без платного корпоративного доступа.

Контракт функции не меняется: fetch_image_url(keywords) -> Optional[str].
"""

import hashlib
import logging
import os
import random
from typing import List, Optional

import httpx

logger = logging.getLogger("slideforge.image_fetcher")

PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
PIXABAY_URL = "https://pixabay.com/api/"

PICSUM_BASE = "https://picsum.photos"

FALLBACK_GRADIENTS = [
    ("#6366f1", "#8b5cf6"),
    ("#0ea5e9", "#22d3ee"),
    ("#f59e0b", "#ef4444"),
    ("#10b981", "#3b82f6"),
    ("#ec4899", "#f43f5e"),
    ("#334155", "#0f172a"),
]


def random_gradient() -> tuple:
    return random.choice(FALLBACK_GRADIENTS)


def _seed_from_keywords(keywords: List[str]) -> str:
    joined = "-".join(keywords) or "presentator"
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]


async def _fetch_from_pixabay(keywords: List[str]) -> Optional[str]:
    if not PIXABAY_API_KEY or not keywords:
        return None

    query = " ".join(keywords[:3])
    params = {
        "key": PIXABAY_API_KEY,
        "q": query,
        "image_type": "photo",
        "orientation": "horizontal",
        "safesearch": "true",
        "order": "popular",  # более узнаваемые/качественные фото вместо случайных по дате загрузки
        "per_page": 15,
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(PIXABAY_URL, params=params)
    except httpx.RequestError as exc:
        logger.warning("Pixabay недоступен: %s", exc)
        return None

    if resp.status_code == 429:
        logger.warning("Превышен лимит Pixabay API.")
        return None
    if resp.status_code != 200:
        logger.warning("Pixabay вернул %s для запроса '%s'", resp.status_code, query)
        return None

    data = resp.json()
    hits = data.get("hits", [])
    if not hits:
        return None

    photo = hits[0]  # order=popular — берём самое релевантное/качественное, а не случайное
    return photo.get("largeImageURL") or photo.get("webformatURL")


def _picsum_fallback_url(keywords: List[str]) -> Optional[str]:
    if not keywords:
        return None
    seed = _seed_from_keywords(keywords)
    return f"{PICSUM_BASE}/seed/{seed}/1600/1000"


async def fetch_image_url(keywords: List[str]) -> Optional[str]:
    """
    Возвращает URL тематического изображения. Пытается Pixabay (по смыслу),
    при неудаче — Picsum (без поиска по смыслу, но всегда доступен без ключа).
    """
    pixabay_url = await _fetch_from_pixabay(keywords)
    if pixabay_url:
        return pixabay_url
    return _picsum_fallback_url(keywords)
