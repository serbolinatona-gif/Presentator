"""
image_fetcher.py
Подбор изображений через Picsum Photos (picsum.photos) — бесплатный сервис
случайных стоковых фото БЕЗ ключа и БЕЗ регистрации. Работает из любой страны.

Компромисс: в отличие от Pexels, Picsum не умеет искать по ключевым словам —
он просто отдаёт случайное фото по "seed" (детерминированному идентификатору).
Чтобы картинки хотя бы не дублировались бессмысленно и было что-то похожее на
привязку к теме слайда, в качестве seed используются ключевые слова слайда:
один и тот же набор ключевых слов всегда даёт одно и то же фото.

Если понадобится подбор именно по смыслу (а не просто симпатичная случайная
картинка) — можно позже подключить Pixabay API (тоже бесплатный, часто
регистрируется проще, чем Pexels) без изменений в остальном коде: контракт
fetch_image_url(keywords) -> Optional[str] остаётся прежним.
"""

import hashlib
import logging
import random
from typing import List, Optional

logger = logging.getLogger("slideforge.image_fetcher")

PICSUM_BASE = "https://picsum.photos"

# Пары цветов для градиентной заглушки, если картинку решили не использовать
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
    joined = "-".join(keywords) or "slideforge"
    # короткий стабильный хэш, чтобы seed был компактным и URL-safe
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]


async def fetch_image_url(keywords: List[str]) -> Optional[str]:
    """
    Возвращает URL изображения 1920x1080 с Picsum, детерминированный по ключевым словам.
    Не делает сетевых запросов сама — Picsum отдаёт картинку напрямую по URL,
    поэтому здесь просто конструируется прямая ссылка.
    """
    if not keywords:
        return None
    seed = _seed_from_keywords(keywords)
    return f"{PICSUM_BASE}/seed/{seed}/1920/1080"
