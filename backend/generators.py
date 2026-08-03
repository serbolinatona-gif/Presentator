"""
generators.py
Работа с GigaChat API (Сбер): генерация структуры презентации и наполнение слайдов.

Ключевые исправления в этой версии:
- outline генерируется ПАЧКАМИ (batch) по 5 слайдов за раз, а не всё разом.
  Причина бага ">5 слайдов не работает": при большом slide_count модель обрезала
  ответ по лимиту токенов, JSON обрывался посреди строки — отсюда ошибки с кавычками
  и JSONDecodeError. Батчами эта проблема исчезает, т.к. каждый запрос компактный.
- Добавлен явный max_tokens в запросах к GigaChat, чтобы не обрезался ответ.
- Добавлена ретрай-логика (до 3 попыток) с "самоисправлением" промпта на основе
  текста ошибки парсинга.
- Добавлен fallback-репарсер JSON (правит частые проблемы: висячие запятые,
  "умные" кавычки, неэкранированные переносы строк).
- Добавлена проверка количества слайдов в outline — если меньше запрошенного,
  дозапрашиваются недостающие.
- Добавлена поддержка "детального промпта" (доп. требования пользователя).
- Промпты переписаны на более качественный, структурированный, "вузовский" контент.
"""

import json
import logging
import os
import re
import time
import uuid
from typing import List, Optional

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger("slideforge.generators")

GIGACHAT_AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY", "")
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat")

GIGACHAT_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

GIGACHAT_VERIFY_SSL = os.getenv("GIGACHAT_VERIFY_SSL", "false").lower() == "true"

MAX_RETRIES = 3
OUTLINE_BATCH_SIZE = 5  # генерируем структуру пачками, чтобы не упираться в лимит токенов

_token_cache = {"access_token": None, "expires_at": 0}


class GenerationError(Exception):
    """Ошибка генерации контента (например, AI не ответил или вернул невалидный JSON)."""


class SlideOutline(BaseModel):
    title: str
    key_points: List[str] = Field(default_factory=list)


class SlideContent(BaseModel):
    title: str
    subtitle: Optional[str] = None
    bullets: List[str] = Field(default_factory=list)
    image_keywords: List[str] = Field(default_factory=list)
    speaker_notes: Optional[str] = None


async def _get_gigachat_token() -> str:
    """Получает (и кэширует) access_token GigaChat через OAuth2 client_credentials."""
    if not GIGACHAT_AUTH_KEY:
        raise GenerationError(
            "GIGACHAT_AUTH_KEY не задан. Добавь Authorization key из личного кабинета "
            "developers.sber.ru/studio в переменные окружения."
        )

    now = time.time()
    if _token_cache["access_token"] and _token_cache["expires_at"] - 30 > now:
        return _token_cache["access_token"]

    headers = {
        "Authorization": f"Basic {GIGACHAT_AUTH_KEY}",
        "RqUID": str(uuid.uuid4()),
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    data = {"scope": GIGACHAT_SCOPE}

    async with httpx.AsyncClient(timeout=30.0, verify=GIGACHAT_VERIFY_SSL) as client:
        try:
            resp = await client.post(GIGACHAT_OAUTH_URL, headers=headers, data=data)
        except httpx.RequestError as exc:
            logger.error("Ошибка сети при получении токена GigaChat: %s", exc)
            raise GenerationError(
                "Не удалось связаться с GigaChat OAuth. Проверь интернет-соединение."
            ) from exc

    if resp.status_code != 200:
        logger.error("GigaChat OAuth вернул ошибку %s: %s", resp.status_code, resp.text)
        raise GenerationError(
            "Не удалось авторизоваться в GigaChat. Проверь GIGACHAT_AUTH_KEY и срок его действия."
        )

    payload = resp.json()
    token = payload.get("access_token")
    expires_at = payload.get("expires_at", now + 1800)
    if not token:
        raise GenerationError("GigaChat не вернул access_token.")

    _token_cache["access_token"] = token
    _token_cache["expires_at"] = expires_at / 1000 if expires_at > 10**12 else expires_at
    return token


def _extract_json(text: str) -> str:
    """Достаём JSON из ответа модели, даже если он обёрнут в ```json ... ```."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("[") if "[" in text else text.find("{")
    end = max(text.rfind("]"), text.rfind("}"))
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return text


def _repair_json(text: str) -> str:
    """
    Чинит частые проблемы в JSON, который возвращают LLM:
    - "умные" кавычки вместо прямых
    - висячие запятые перед ] или }
    - буквальные переносы строк внутри строковых значений
    """
    fixed = text
    fixed = fixed.replace(""", '"').replace(""", '"').replace("'", "'")
    fixed = re.sub(r",\s*([\]}])", r"\1", fixed)  # висячие запятые

    # Экранируем "голые" переводы строк внутри строковых литералов
    def _escape_newlines_in_strings(match):
        return match.group(0).replace("\n", "\\n").replace("\r", "")

    fixed = re.sub(r'"(?:[^"\\]|\\.)*"', _escape_newlines_in_strings, fixed, flags=re.DOTALL)
    return fixed


def _parse_json_loose(raw_text: str) -> Optional[object]:
    """Пытается распарсить JSON: сначала как есть, потом после починки."""
    candidate = _extract_json(raw_text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_repair_json(candidate))
    except json.JSONDecodeError:
        return None


async def _call_gigachat(prompt: str, temperature: float = 0.7, max_tokens: int = 2000) -> str:
    token = await _get_gigachat_token()

    payload = {
        "model": GIGACHAT_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты помощник, который отвечает СТРОГО валидным JSON без markdown-разметки, "
                    "без ```-оберток и без пояснений до/после JSON. Все кавычки внутри текстовых "
                    "значений экранируй символом \\. Никогда не обрывай JSON на середине."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "n": 1,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=90.0, verify=GIGACHAT_VERIFY_SSL) as client:
        try:
            resp = await client.post(GIGACHAT_CHAT_URL, headers=headers, json=payload)
        except httpx.RequestError as exc:
            logger.error("Ошибка сети при обращении к GigaChat: %s", exc)
            raise GenerationError(
                "Не удалось связаться с GigaChat API. Проверь интернет-соединение."
            ) from exc

    if resp.status_code == 429:
        raise GenerationError("Превышен бесплатный лимит запросов к GigaChat. Попробуй через минуту.")
    if resp.status_code == 401:
        _token_cache["access_token"] = None
        raise GenerationError("GigaChat отклонил авторизацию. Попробуй сгенерировать ещё раз.")
    if resp.status_code != 200:
        logger.error("GigaChat вернул ошибку %s: %s", resp.status_code, resp.text)
        raise GenerationError(f"GigaChat API вернул ошибку {resp.status_code}.")

    data = resp.json()
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        logger.error("Неожиданный формат ответа GigaChat: %s", data)
        raise GenerationError("AI вернул пустой или некорректный ответ.") from exc

    if not text.strip():
        raise GenerationError("AI не ответил. Попробуй ещё раз.")

    return text


async def _call_gigachat_json(
    build_prompt, temperature: float = 0.7, max_tokens: int = 2000
) -> object:
    """
    Универсальная обёртка с ретраями: вызывает build_prompt(previous_error) -> str,
    парсит JSON (с починкой), при неудаче повторяет до MAX_RETRIES раз, передавая
    модели текст предыдущей ошибки, чтобы она сама исправилась.
    """
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        prompt = build_prompt(last_error)
        raw = await _call_gigachat(prompt, temperature=temperature, max_tokens=max_tokens)
        parsed = _parse_json_loose(raw)
        if parsed is not None:
            return parsed
        logger.warning(
            "Попытка %d/%d: не удалось распарсить JSON от GigaChat. Фрагмент: %s",
            attempt, MAX_RETRIES, raw[:300],
        )
        last_error = "Ответ не был валидным JSON. Пример проблемы: незакрытая кавычка или обрыв текста."

    raise GenerationError(
        "AI несколько раз подряд вернул некорректный JSON. Попробуй ещё раз или уменьши число слайдов."
    )


async def generate_outline(
    topic: str,
    slide_count: int,
    language: str = "ru",
    detailed_prompt: Optional[str] = None,
) -> List[SlideOutline]:
    """
    Этап 1: структура презентации. Генерируется ПАЧКАМИ по OUTLINE_BATCH_SIZE слайдов,
    чтобы избежать обрыва JSON на больших slide_count.
    """
    lang_instruction = "русском" if language == "ru" else "английском"
    extra = f" Дополнительные требования пользователя: {detailed_prompt.strip()}." if detailed_prompt else ""

    outline: List[SlideOutline] = []
    remaining = slide_count

    while remaining > 0:
        batch_size = min(OUTLINE_BATCH_SIZE, remaining)
        start_index = len(outline) + 1
        is_first_batch = start_index == 1
        is_last_batch = start_index + batch_size - 1 >= slide_count

        role_hint = ""
        if is_first_batch:
            role_hint = " Слайд №1 в этой пачке — титульный (тема + подзаголовок)."
        if is_last_batch:
            role_hint += " Последний слайд в этой пачке — заключение/выводы/спасибо."

        def build_prompt(prev_error, batch_size=batch_size, start_index=start_index, role_hint=role_hint):
            error_note = f" ВНИМАНИЕ: предыдущая попытка провалилась ({prev_error}). Будь особенно аккуратен с JSON." if prev_error else ""
            return (
                f"Ты — эксперт по подготовке университетских и профессиональных презентаций. "
                f"Составь структуру ЧАСТИ презентации на {lang_instruction} языке на тему: \"{topic}\".{extra} "
                f"Это слайды с {start_index} по {start_index + batch_size - 1} из {slide_count} общих.{role_hint} "
                f"Контент должен быть содержательным, конкретным, по существу — без общих расплывчатых фраз, "
                f"уровня презентации в топовом вузе (структурировано, с фактурой, терминами по теме). "
                f'Верни строго JSON-массив из {batch_size} объектов вида '
                '{"title": "заголовок слайда", "key_points": ["конкретный тезис 1", "конкретный тезис 2", "конкретный тезис 3"]}. '
                "Каждый key_point — законченная содержательная мысль, а не общее слово. "
                "Все кавычки внутри текста экранируй \\\". Без markdown, без пояснений, только валидный JSON."
                f"{error_note}"
            )

        items = await _call_gigachat_json(build_prompt, temperature=0.7, max_tokens=1800)
        if not isinstance(items, list):
            raise GenerationError("AI вернул структуру в неожиданном формате (ожидался список слайдов).")

        for item in items[:batch_size]:
            outline.append(
                SlideOutline(
                    title=item.get("title", f"Слайд {len(outline) + 1}"),
                    key_points=item.get("key_points", []),
                )
            )
        remaining = slide_count - len(outline)

    # Финальная защита: если модель всё же не добрала/перебрала — подрезаем/дозаполняем
    if len(outline) > slide_count:
        outline = outline[:slide_count]
    elif len(outline) < slide_count:
        missing = slide_count - len(outline)
        logger.warning("Не хватило %d слайдов в outline — дозаполняем шаблонными.", missing)
        for i in range(missing):
            outline.append(SlideOutline(title=f"Слайд {len(outline) + 1}", key_points=[]))

    return outline


async def generate_slide_content(
    topic: str,
    outline_item: SlideOutline,
    style: str,
    language: str = "ru",
    with_notes: bool = False,
    detailed_prompt: Optional[str] = None,
) -> SlideContent:
    """Этап 2: наполнение конкретного слайда — качественный, структурированный контент."""
    lang_instruction = "русском" if language == "ru" else "английском"
    notes_instruction = (
        'Добавь поле "speaker_notes" (2-3 содержательных предложения для докладчика).'
        if with_notes
        else 'Поле "speaker_notes" оставь пустой строкой.'
    )
    extra = f" Учитывай доп. требования пользователя: {detailed_prompt.strip()}." if detailed_prompt else ""

    def build_prompt(prev_error):
        error_note = f" ВНИМАНИЕ: предыдущая попытка провалилась ({prev_error}). Верни строго валидный JSON." if prev_error else ""
        return (
            f"Ты готовишь слайд для презентации уровня топового университета на тему '{topic}' "
            f"в визуальном стиле '{style}'.{extra} "
            f"Раскрой слайд с заголовком '{outline_item.title}' и тезисами {outline_item.key_points} "
            f"на {lang_instruction} языке. "
            "Требования к качеству контента: конкретные факты/аргументы/примеры, никакой воды и общих фраз, "
            "структура как в реальных профессиональных презентациях (тезис → раскрытие). "
            'Верни строго JSON-объект вида {"title": "заголовок до 8 слов", '
            '"subtitle": "короткий подзаголовок или пустая строка", '
            '"bullets": ["содержательный пункт 1", "содержательный пункт 2", "содержательный пункт 3"], '
            '"image_keywords": ["keyword1", "keyword2"], '
            '"speaker_notes": "..."}. '
            f"{notes_instruction} Пунктов должно быть 3-5, каждый — законченная мысль (до 16 слов), "
            "без сокращений вроде 'и т.д.'. "
            "image_keywords — на английском, 2-3 конкретных визуальных слова по смыслу слайда (не абстрактные). "
            "Все кавычки внутри текста экранируй \\\". Без markdown, только валидный JSON."
            f"{error_note}"
        )

    item = await _call_gigachat_json(build_prompt, temperature=0.7, max_tokens=900)
    if not isinstance(item, dict):
        raise GenerationError("AI вернул слайд в неожиданном формате.")

    return SlideContent(
        title=item.get("title") or outline_item.title,
        subtitle=item.get("subtitle") or None,
        bullets=item.get("bullets") or outline_item.key_points,
        image_keywords=(item.get("image_keywords") or [])[:3],
        speaker_notes=item.get("speaker_notes") or None,
    )
