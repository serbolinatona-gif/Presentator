
# coding: utf-8
"""
generators.py — генерация структуры презентации и наполнение слайдов через GigaChat.
Переписан для максимальной устойчивости к часто ломаемому JSON у GigaChat.

Ключевые идеи:
- Промпты запрещают использование двойных кавычек внутри текстовых значений.
- Попытки парсинга: json -> json5 -> demjson3 -> эвристический repair -> финальные попытки.
- Ретрай: при невалидном JSON следующий запрос посылает предыдущий ответ модели
  и краткую инструкцию: "You returned invalid JSON; fix only formatting, preserve content."
- Если outline batch не парсится, автоматически уменьшаем batch size: 5 -> 3 -> 2 -> 1.
- Валидируем и нормализуем структуру (допускаем как списки, так и строки для списковых полей).
- Публичные функции generate_outline и generate_slide_content сохранены по сигнатуре.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from typing import List, Optional, Any

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger("slideforge.generators")
logger.addHandler(logging.NullHandler())

# Try optional relaxed JSON libraries for more robust parsing (best-effort).
json5 = None
demjson3 = None
try:
    import json5 as _json5

    json5 = _json5
except Exception:
    json5 = None

try:
    import demjson3 as _demjson3

    demjson3 = _demjson3
except Exception:
    demjson3 = None

GIGACHAT_AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY", "")
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat")

GIGACHAT_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

GIGACHAT_VERIFY_SSL = os.getenv("GIGACHAT_VERIFY_SSL", "false").lower() == "true"

MAX_RETRIES = 3
OUTLINE_BATCH_SIZE = int(os.getenv("OUTLINE_BATCH_SIZE", "5"))
# Fallback shrinking sequence for batch sizes (ordered)
BATCH_SHRINK_SEQUENCE = [5, 3, 2, 1]

_token_cache: dict = {"access_token": None, "expires_at": 0}

# Public models (unchanged API)
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


# -----------------------
# Auth
# -----------------------
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
    # API might return either seconds or ms
    _token_cache["expires_at"] = expires_at / 1000 if expires_at > 10**12 else expires_at
    return token


# -----------------------
# Helpers: extract / repair / parse JSON (very robust)
# -----------------------
def _extract_json(text: str) -> str:
    """
    Выделяем предполагаемый JSON-фрагмент из произвольного текста:
    - убираем блоки ```...```
    - находим первую { или [ и последнюю } или ] и обрезаем вокруг них
    """
    if not text:
        return text
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    # Find first JSON opener and last closer
    first_obj = min(
        (idx for idx in (text.find("["), text.find("{")) if idx != -1),
        default=-1,
    )
    if first_obj == -1:
        # nothing that looks like JSON; return original (to give repair a chance)
        return text
    # find last matching close bracket (prefer } or ])
    last_obj = max(text.rfind("]"), text.rfind("}"))
    if last_obj == -1:
        last_obj = len(text) - 1
    return text[first_obj : last_obj + 1]


def _heuristic_repair(text: str) -> str:
    """
    Попытка починить часто встречающиеся ошибки LLM:
    - «умные» кавычки -> прямые
    - лишние/дубльные слэши
    - висячие запятые
    - двойные запятые
    - незакрытые строки (попытаться закрыть)
    - балансировка скобок
    - убираем управляющие символы
    """
    if not text:
        return text
    s = text

    # Normalize different quote types to ASCII quotes
    s = s.replace("“", '"').replace("”", '"').replace("„", '"').replace("«", '"').replace("»", '"')
    s = s.replace("‘", "'").replace("’", "'")

    # Remove control characters except \n and \t (which might be inside strings)
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", "", s)

    # Replace sequences of escaped newlines that can break JSON into explicit \n
    s = s.replace("\\\n", "\\n").replace("\\\r\n", "\\n")

    # Collapse repeated backslashes (e.g. \\\")
    s = re.sub(r"\\\\+", r"\\", s)

    # Replace patterns like '",",' or '", ",' that appear from broken joins
    s = re.sub(r'"\s*,\s*"', '","', s)

    # Remove trailing commas before closing brackets/braces: ,] or ,}
    s = re.sub(r",\s*([\]\}])", r"\1", s)

    # Collapse accidental ',,'
    s = re.sub(r",\s*,+", ",", s)

    # If there are unescaped newlines inside quotes, try to escape them
    def _escape_newlines_in_string_literals(m):
        content = m.group(0)
        # inside the matched quotes, escape raw newlines
        content = content.replace("\n", "\\n").replace("\r", "")
        return content

    s = re.sub(r'"((?:[^"\\]|\\.)*)"', _escape_newlines_in_string_literals, s, flags=re.DOTALL)

    # Attempt to balance quotes: if odd number of double quotes, append a quote before final bracket
    if s.count('"') % 2 == 1:
        # try to insert a closing quote before final brace/bracket if possible
        last_close = max(s.rfind("}"), s.rfind("]"))
        if last_close != -1:
            s = s[: last_close] + '"' + s[last_close:]
        else:
            s = s + '"'

    # Balance braces/brackets by appending closers if there are more openers
    open_braces = s.count("{") - s.count("}")
    open_brackets = s.count("[") - s.count("]")
    if open_braces > 0:
        s += "}" * open_braces
    if open_brackets > 0:
        s += "]" * open_brackets

    return s


def _try_parse_with_relaxed_builders(candidate: str) -> Optional[Any]:
    """Пробуем разные парсеры: json -> json5 -> demjson3"""
    # Try strict json
    try:
        return json.loads(candidate)
    except Exception:
        pass

    # Try json5 (accepts single quotes, trailing commas, etc.)
    if json5 is not None:
        try:
            return json5.loads(candidate)
        except Exception:
            pass

    # Try demjson3 (very tolerant)
    if demjson3 is not None:
        try:
            return demjson3.decode(candidate)
        except Exception:
            pass

    return None


def _parse_json_loose(raw_text: str) -> Optional[Any]:
    """
    Robust parsing flow:
    1) extract likely JSON fragment
    2) try strict json
    3) try relaxed parsers (json5/demjson3)
    4) try heuristic repairs and re-parse
    """
    candidate = _extract_json(raw_text)

    # 1. direct attempts
    parsed = _try_parse_with_relaxed_builders(candidate)
    if parsed is not None:
        return parsed

    # 2. heuristic-repair attempts (several passes)
    repaired = _heuristic_repair(candidate)
    parsed = _try_parse_with_relaxed_builders(repaired)
    if parsed is not None:
        return parsed

    # 3. more aggressive: remove non-ASCII trailing garbage and retry
    stripped = re.sub(r"[^\x00-\x7F]+$", "", repaired).strip()
    if stripped != repaired:
        parsed = _try_parse_with_relaxed_builders(stripped)
        if parsed is not None:
            return parsed

    return None


# -----------------------
# Low-level GigaChat call
# -----------------------
async def _call_gigachat(prompt: str, temperature: float = 0.7, max_tokens: int = 2000) -> str:
    """
    Выполняет HTTP-вызов к GigaChat и возвращает сырой текст ответа.
    Системная роль строго просит вернуть JSON (но НЕ требует от модели экранирования кавычек).
    Важно: мы заранее запрещаем использование " внутри текстовых значений в промптах.
    """
    token = await _get_gigachat_token()

    payload = {
        "model": GIGACHAT_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    # Упрощённая, строгая системная роль: запрещаем markdown и любые пояснения.
                    # Просим модель вернуть только JSON и НЕ использовать двойные кавычки внутри текстов.
                    "Ты — сервисный ассистент. ОТВЕЧАЙ ТОЛЬКО ЧИСТЫМ JSON (в теле ответа). "
                    "Не добавляй markdown, не добавляй никакой текст до или после JSON. "
                    "ВАЖНО: внутри строковых значений НЕ ИСПОЛЬЗУЙ двойные кавычки (\"). "
                    "Если нужно упомянуть название, используй апострофы или не оборачивай в кавычки. "
                    "Не выдумывай дополнительные поля, верни только те поля, которые запрошены в задаче."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "n": 1,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
    }

    async with httpx.AsyncClient(timeout=90.0, verify=GIGACHAT_VERIFY_SSL) as client:
        try:
            resp = await client.post(GIGACHAT_CHAT_URL, headers=headers, json=payload)
        except httpx.RequestError as exc:
            logger.error("Ошибка сети при обращении к GigaChat: %s", exc)
            raise GenerationError("Не удалось связаться с GigaChat API. Проверь интернет-соединение.") from exc

    if resp.status_code == 429:
        raise GenerationError("Превышен лимит запросов к GigaChat. Попробуй через минуту.")
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

    if not text or not text.strip():
        raise GenerationError("AI не ответил. Попробуй ещё раз.")

    return text


# -----------------------
# Higher-level wrapper: делаем ретраи, при ошибке посылаем предыдущий ответ модели
# -----------------------
async def _call_gigachat_json(build_prompt, temperature: float = 0.7, max_tokens: int = 2000) -> Any:
    """
    build_prompt(previous_response: Optional[str]) -> str

    Поведение:
    - При первой попытке previous_response == None, формируем обычный prompt.
    - Если JSON не распарсился, берем сырую строку ответа и при следующей попытке
      посылаем её модели вместе с инструкцией: "You returned invalid JSON; fix only formatting, preserve content."
    - Повторяем до MAX_RETRIES раз.
    """
    previous_response: Optional[str] = None
    last_raw: Optional[str] = None

    for attempt in range(1, MAX_RETRIES + 1):
        prompt = build_prompt(previous_response)
        raw = await _call_gigachat(prompt, temperature=temperature, max_tokens=max_tokens)
        last_raw = raw
        parsed = _parse_json_loose(raw)
        if parsed is not None:
            return parsed

        # Если не распарсили — подготовить previous_response для следующей итерации
        logger.warning(
            "Попытка %d/%d: не удалось распарсить JSON. Фрагмент: %.300s",
            attempt, MAX_RETRIES, raw,
        )
        # Для следующего запроса мы передадим непосредственно текст предыдущего ответа
        # и короткую инструкцию: исправь формат, не меняй содержание.
        previous_response = raw

    # Если вышли из цикла — не удалось получить валидный JSON
    raise GenerationError(
        "AI несколько раз подряд вернул некорректный JSON. Попробуй ещё раз или уменьши число слайдов."
    )


# -----------------------
# Normalization & validation helpers
# -----------------------
def _ensure_list_of_strings(value: Any) -> List[str]:
    """
    Нормализует поле, которое может быть списком строк или одной строкой:
    - Если list — приводим все элементы к str и убираем пустые
    - Если str — делим по переводам строки либо по точке с запятой, либо по явным маркерам
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        # Split heuristics: try newlines first, then semicolons, then '||' etc.
        if "\n" in raw:
            parts = [p.strip() for p in raw.splitlines() if p.strip()]
            if len(parts) > 1:
                return parts
        if ";" in raw:
            parts = [p.strip() for p in raw.split(";") if p.strip()]
            if len(parts) > 1:
                return parts
        # comma-separated fallback (but avoid splitting if commas are used in sentences)
        if raw.count(",") >= 2 and len(raw) > 80:
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            if len(parts) > 1:
                return parts
        # fallback: return as single-item list
        return [raw]
    # any other type — coerce to string
    return [str(value).strip()]


def _validate_and_normalize_outline(items: Any, expected_count: int) -> List[SlideOutline]:
    """
    Проверяем, что outline — список объектов с title и key_points.
    Нормализуем key_points в список строк.
    """
    if not isinstance(items, list):
        raise GenerationError("AI вернул outline в неожиданном формате (ожидался список).")

    result: List[SlideOutline] = []
    for idx, obj in enumerate(items):
        if not isinstance(obj, dict):
            raise GenerationError(f"Outline: элемент {idx} не является объектом.")
        title = obj.get("title") or obj.get("heading") or f"Слайд {len(result) + 1}"
        key_points_raw = obj.get("key_points") or obj.get("keyPoints") or obj.get("points") or []
        key_points = _ensure_list_of_strings(key_points_raw)
        result.append(SlideOutline(title=str(title).strip(), key_points=key_points))

    # If model returned fewer than expected — that's acceptable; caller will fill placeholders
    return result


def _validate_and_normalize_slide(obj: Any) -> SlideContent:
    """
    Проверяем и нормализуем слайд: объект со строковыми title, subtitle, bullets (list), image_keywords (list), speaker_notes.
    """
    if not isinstance(obj, dict):
        raise GenerationError("AI вернул слайд в неожиданном формате (ожидался объект).")
    title = obj.get("title") or obj.get("heading") or ""
    subtitle = obj.get("subtitle") or obj.get("subheading") or ""
    bullets_raw = obj.get("bullets") or obj.get("bullet_points") or obj.get("points") or []
    bullets = _ensure_list_of_strings(bullets_raw)
    image_keywords_raw = obj.get("image_keywords") or obj.get("imageKeywords") or obj.get("images") or []
    # image_keywords expected in English — normalize to lower-case short tokens
    image_keywords = [str(x).strip() for x in (image_keywords_raw if isinstance(image_keywords_raw, list) else _ensure_list_of_strings(image_keywords_raw))]
    image_keywords = [re.sub(r"[^\w\s-]", "", k).lower() for k in image_keywords if k]
    image_keywords = image_keywords[:3]
    speaker_notes = obj.get("speaker_notes") or obj.get("notes") or ""
    return SlideContent(
        title=str(title).strip() or " ",
        subtitle=str(subtitle).strip() or None,
        bullets=bullets or [],
        image_keywords=image_keywords,
        speaker_notes=str(speaker_notes).strip() or None,
    )


# -----------------------
# Public API
# -----------------------
async def generate_outline(
    topic: str,
    slide_count: int,
    language: str = "ru",
    detailed_prompt: Optional[str] = None,
) -> List[SlideOutline]:
    """
    Генерирует структуру презентации пачками (batch), автоматически уменьшая batch size при ошибках.
    Возвращает список SlideOutline длины slide_count (заполняет шаблонными, если модель не выдала нужного числа).
    """
    if slide_count <= 0:
        return []

    lang_instruction = "на русском" if language == "ru" else "in English"
    extra = f" Дополнительные требования: {detailed_prompt.strip()}." if detailed_prompt else ""

    outline: List[SlideOutline] = []
    remaining = slide_count

    # Prepare shrink sequence based on OUTLINE_BATCH_SIZE. Ensure it's ordered descending and contains 1.
    shrink_seq = [b for b in BATCH_SHRINK_SEQUENCE if b <= max(OUTLINE_BATCH_SIZE, max(BATCH_SHRINK_SEQUENCE))]
    if 1 not in shrink_seq:
        shrink_seq.append(1)
    shrink_seq = sorted(set(shrink_seq), reverse=True)

    while remaining > 0:
        # choose starting batch size (cap by remaining)
        desired_batch = min(OUTLINE_BATCH_SIZE, remaining)
        # find first allowed in shrink_seq <= desired_batch
        batch_candidates = [b for b in shrink_seq if b <= desired_batch]
        if not batch_candidates:
            batch_candidates = [1]
        batch_size = batch_candidates[0]

        success = False
        # try shrinking batch_size if necessary
        for candidate in (b for b in batch_candidates):
            start_index = len(outline) + 1
            end_index = start_index + candidate - 1
            is_first_batch = start_index == 1
            is_last_batch = end_index >= slide_count

            role_hint = ""
            if is_first_batch:
                role_hint = "Первый слайд — титульный (тема + подзаголовок)."
            if is_last_batch:
                role_hint += " Последний слайд — заключение/выводы/спасибо."

            # build_prompt expects previous_response or None
            def build_prompt(previous_response: Optional[str], batch=candidate, start=start_index, role_hint=role_hint):
                """
                Для первой попытки previous_response == None — обычный запрос.
                При previous_response != None — мы передаем предыдущий сырой ответ модели и просьбу исправить только формат.
                """
                if previous_response:
                    # для корректировки — отправляем предыдущий ответ модели и спрашиваем только про исправление формата
                    return (
                        "Я пришлю тебе ранее сгенерированный JSON. Он невалиден. "
                        "Исправь ТОЛЬКО формат так, чтобы это был валидный JSON, "
                        "не меняй содержание и порядок элементов, не добавляй и не убирай пункты. "
                        "Ответь только исправленным JSON (ничего кроме JSON)."
                        f"\n\n=== PREVIOUS_RESPONSE_START ===\n{previous_response}\n=== PREVIOUS_RESPONSE_END ==="
                    )

                # Первый промпт: просим коротко и просто, запрещаем двойные кавычки внутри текстов
                return (
                    f"Составь структуру части презентации {lang_instruction} по теме: \"{topic}\".{extra} "
                    f"Это слайды с {start} по {start + batch - 1} из {slide_count}. {role_hint} "
                    "Требования: академический / профессиональный уровень — конкретика, термины, факты, без общих фраз. "
                    "ОТВЕТ: строго JSON-массив (список) из объектов. Каждый объект должен иметь поля: "
                    '"title" (короткая строка), "key_points" (список тезисов). '
                    "ВАЖНО: внутри значений НЕ ИСПОЛЬЗУЙ двойные кавычки (\"). "
                    "Не добавляй других полей, не добавляй пояснений или markdown — только JSON."
                )

            try:
                items_raw = await _call_gigachat_json(build_prompt, temperature=0.7, max_tokens=1800)
            except GenerationError as exc:
                logger.warning("Batch size %d failed: %s", candidate, exc)
                # попробуем следующий, меньший batch
                continue

            # Validate & normalize
            try:
                items = _validate_and_normalize_outline(items_raw, candidate)
            except GenerationError as exc:
                logger.warning("Validation failed for batch size %d: %s", candidate, exc)
                # try smaller batch
                continue

            # Append up to candidate items (model may return more — truncate)
            for obj in items[:candidate]:
                outline.append(obj)
            remaining = slide_count - len(outline)
            success = True
            break  # exit shrink loop

        if not success:
            # если ни одна градация batch не сработала — добавляем placeholder(s) и продолжаем
            logger.error("Не удалось получить корректный batch для слайдов %d..; добавляем заглушки.", len(outline) + 1)
            outline.append(SlideOutline(title=f"Слайд {len(outline) + 1}", key_points=[]))
            remaining = slide_count - len(outline)

    # Trim or pad to exact slide_count
    if len(outline) > slide_count:
        outline = outline[:slide_count]
    elif len(outline) < slide_count:
        missing = slide_count - len(outline)
        logger.warning("Не хватило %d слайдов — добавляем шаблонные.", missing)
        for _ in range(missing):
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
    """
    Наполняет конкретный слайд. Возвращает SlideContent.
    Поведение ретраев аналогично: при парсинг-ошибке отправляем предыдущий ответ модели и просим исправить только формат.
    Допускаем, что bullets/image_keywords могут приходить как строки — нормализуем.
    """
    lang_instruction = "на русском" if language == "ru" else "in English"
    extra = f" Дополнительные требования: {detailed_prompt.strip()}." if detailed_prompt else ""
    notes_instruction = (
        "Добавь поле speaker_notes (2-3 содержательных предложения)." if with_notes else "speaker_notes можно вернуть пустым."
    )

    def build_prompt(previous_response: Optional[str]):
        if previous_response:
            return (
                "Ты только что сгенерировал JSON для одного слайда, но он оказался невалидным. "
                "Исправь ТОЛЬКО формат, сделай JSON валидным, не меняй содержание и порядок полей. "
                "Ответь только исправленным JSON (ничего лишнего).\n\n"
                f"=== PREVIOUS_RESPONSE_START ===\n{previous_response}\n=== PREVIOUS_RESPONSE_END ==="
            )
        # First attempt: full generation prompt
        return (
            f"Подготовь содержимое одного слайда {lang_instruction} для презентации на тему: \"{topic}\". {extra} "
            f"Стиль визуальной подачи: '{style}'. Титул слайда: '{outline_item.title}'. "
            f"Если у тебя есть тезисы: {outline_item.key_points}, используй их. {notes_instruction} "
            "Требования к ответу: верни строго JSON-объект с полями: "
            '"title" (до 8 слов, без двойных кавычек внутри), '
            '"subtitle" (короткая строка или пустая строка), '
            '"bullets" (список 3-5 коротких пунктов, каждая не более ~16 слов), '
            '"image_keywords" (2-3 коротких ключевых слова на английском, для поиска изображения), '
            '"speaker_notes" (строка или пустая). '
            "НЕ ДОБАВЛЯЙ НИЧЕГО КРОМЕ УКАЗАННЫХ ПОЛЕЙ. ОТВЕЧАЙ ТОЛЬКО JSON."
        )

    item_raw = await _call_gigachat_json(build_prompt, temperature=0.6, max_tokens=900)
    # Validate & normalize
    slide = _validate_and_normalize_slide(item_raw)
    # Ensure reasonable bullets fallback
    if not slide.bullets and outline_item.key_points:
        slide.bullets = outline_item.key_points[:3]
    return slide
