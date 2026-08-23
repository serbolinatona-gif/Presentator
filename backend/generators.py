"""
generators.py
Работа с GigaChat API (Сбер): генерация структуры презентации и наполнение слайдов
по одной из 9 профессиональных раскладок (layouts) — вместо однотипных bullet-point слайдов.

Архитектура генерации контента:
1. generate_outline()      — батчами получает subtopic-заготовки (title + key_points) на
                              каждый слайд. Это "сырьё", не финальный вид слайда.
2. assign_layouts()        — бэкенд (не ИИ!) детерминированно назначает каждому слайду тип
                              вёрстки: title -> toc -> [ротация из 6 контентных раскладок,
                              без повтора подряд] -> conclusion. Так гарантируется реальное
                              разнообразие вместо "как получится у модели".
3. generate_layout_content() — для каждого слайда генерирует контент СТРОГО под поля нужной
                              раскладки (например, для "quote_context" — quote/context/
                              explanation, а не общие bullets).

Устойчивость к JSON-ошибкам GigaChat (см. предыдущий фикс): модель имеет тенденцию по ошибке
экранировать СТРУКТУРНЫЕ кавычки между элементами массива (например ["a\",\"b"] вместо
["a","b"]). Поэтому промпт прямо запрещает использовать кавычки внутри текста вообще, а
репар-парсер отдельно чинит именно этот паттерн.
"""

import json
import logging
import os
import random
import re
import time
import uuid
from typing import Dict, List, Optional

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
OUTLINE_BATCH_SIZE = 4  # уменьшено с 5: с более длинными промптами (анти-галлюцинации, грамотность)
                        # батч из 5 слайдов иногда обрезался по лимиту токенов

_token_cache = {"access_token": None, "expires_at": 0}


class GenerationError(Exception):
    """Ошибка генерации контента (например, AI не ответил или вернул невалидный JSON)."""


class SlideOutline(BaseModel):
    title: str
    key_points: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Раскладки слайдов
# ---------------------------------------------------------------------------

LAYOUT_TITLE = "title"
LAYOUT_TOC = "toc"
LAYOUT_THESIS_PROOF = "thesis_proof"
LAYOUT_DENSE_TEXT = "dense_text"
LAYOUT_CONCEPT_ANATOMY = "concept_anatomy"
LAYOUT_COMPARISON = "comparison"
LAYOUT_CAUSAL_CHAIN = "causal_chain"
LAYOUT_QUOTE_CONTEXT = "quote_context"
LAYOUT_CONCLUSION = "conclusion"

# Пул раскладок для "содержательных" слайдов (не первого, не оглавления, не последнего)
CONTENT_LAYOUT_POOL = [
    LAYOUT_THESIS_PROOF,
    LAYOUT_DENSE_TEXT,
    LAYOUT_CONCEPT_ANATOMY,
    LAYOUT_COMPARISON,
    LAYOUT_CAUSAL_CHAIN,
    LAYOUT_QUOTE_CONTEXT,
]

# Раскладки, для которых стоит подбирать изображение
LAYOUTS_WITH_IMAGE = {
    LAYOUT_THESIS_PROOF, LAYOUT_DENSE_TEXT, LAYOUT_CONCEPT_ANATOMY,
    LAYOUT_COMPARISON, LAYOUT_CAUSAL_CHAIN, LAYOUT_QUOTE_CONTEXT,
}


def assign_layouts(slide_count: int) -> List[str]:
    """
    Детерминированно (не через ИИ) назначает раскладку каждому слайду:
    0 -> title, 1 -> toc (если слайдов достаточно), последний -> conclusion,
    середина -> ротация CONTENT_LAYOUT_POOL без повтора подряд.
    """
    if slide_count <= 2:
        return [LAYOUT_TITLE] + [LAYOUT_THESIS_PROOF] * (slide_count - 1)

    has_toc = slide_count >= 4
    has_conclusion = slide_count >= 4

    layouts: List[str] = [LAYOUT_TITLE]
    if has_toc:
        layouts.append(LAYOUT_TOC)

    middle_count = slide_count - len(layouts) - (1 if has_conclusion else 0)
    pool = CONTENT_LAYOUT_POOL.copy()
    random.shuffle(pool)
    middle: List[str] = []
    last_layout = None
    i = 0
    while len(middle) < middle_count:
        candidate = pool[i % len(pool)]
        if candidate == last_layout and len(pool) > 1:
            i += 1
            continue
        middle.append(candidate)
        last_layout = candidate
        i += 1
        if i % len(pool) == 0:
            random.shuffle(pool)
    layouts.extend(middle)

    if has_conclusion:
        layouts.append(LAYOUT_CONCLUSION)

    return layouts[:slide_count]


async def _get_gigachat_token() -> str:
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
        detail = ""
        try:
            err_json = resp.json()
            code = err_json.get("code")
            message = err_json.get("message")
            hints = {
                4: "ключ авторизации повреждён/содержит опечатку — проверь, что скопирован целиком, без пробелов и переносов строк.",
                6: "ключ не соответствует выбранному scope — перевыпусти Authorization key в личном кабинете.",
                7: "ключ выдан для другого тарифа/scope, чем указан в GIGACHAT_SCOPE — проверь тариф в личном кабинете.",
                1: "поле scope некорректно — проверь значение GIGACHAT_SCOPE (обычно GIGACHAT_API_PERS).",
            }
            hint = hints.get(code, "")
            detail = f" [код {code}: {message}] {hint}".strip()
        except Exception:
            pass
        raise GenerationError(
            f"Не удалось авторизоваться в GigaChat (HTTP {resp.status_code}).{detail} "
            "Проверь GIGACHAT_AUTH_KEY в Render."
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
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    start = min(starts) if starts else -1
    end = max(text.rfind("]"), text.rfind("}"))
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return text


def _repair_json(text: str) -> str:
    """
    Чинит частые проблемы JSON от GigaChat:
    - "умные" кавычки
    - висячие запятые
    - буквальные переносы строк внутри строк
    - GigaChat периодически по ошибке экранирует СТРУКТУРНЫЕ кавычки
      (разделители между элементами массива), например ["a\",\"b"] вместо ["a","b"].
    - GigaChat иногда забывает закрыть объект перед следующим в массиве:
      ...],{"title":... вместо ...]},{"title":... — чиним и это.
    """
    fixed = text
    fixed = fixed.replace(""", '"').replace(""", '"').replace("'", "'")

    for _ in range(3):
        fixed = re.sub(r'\\"(\s*[,\]}:])', r'"\1', fixed)
        fixed = re.sub(r'([\[{,:]\s*)\\"', r'\1"', fixed)

    # Пропущенная закрывающая } перед началом следующего объекта в массиве
    fixed = re.sub(r"\]\s*,\s*\{", r"]},{", fixed)
    fixed = re.sub(r'"\s*,\s*\{(?=\s*"title")', r'"},{', fixed)

    fixed = re.sub(r",\s*([\]}])", r"\1", fixed)

    def _escape_newlines_in_strings(match):
        return match.group(0).replace("\n", "\\n").replace("\r", "")

    fixed = re.sub(r'"(?:[^"\\]|\\.)*"', _escape_newlines_in_strings, fixed, flags=re.DOTALL)
    return fixed


def _balance_brackets(text: str) -> str:
    """
    Последний рубеж защиты: если ответ обрезался по лимиту токенов (max_tokens),
    JSON останется незакрытым. Досчитываем непарные кавычки/скобки в конце,
    чтобы получить хоть что-то валидное (даже если последний элемент неполный —
    это лучше, чем полный отказ).
    """
    s = text
    if s.count('"') % 2 == 1:
        s += '"'
    open_braces = s.count("{") - s.count("}")
    open_brackets = s.count("[") - s.count("]")
    if open_braces > 0:
        s += "}" * open_braces
    if open_brackets > 0:
        s += "]" * open_brackets
    return s


def _parse_json_loose(raw_text: str) -> Optional[object]:
    candidate = _extract_json(raw_text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    repaired = _repair_json(candidate)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_balance_brackets(repaired))
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
                    "без ```-оберток и без пояснений до/после JSON. НЕ используй кавычки \" "
                    "внутри текстовых значений вообще (ни для цитат, ни для выделения слов) — "
                    "если нужно выделить слово, используй обычный текст без кавычек. "
                    "Кавычки \" разрешены ТОЛЬКО как границы JSON-строк. Никогда не обрывай JSON на середине."
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


async def _call_gigachat_json(build_prompt, temperature: float = 0.7, max_tokens: int = 2000) -> object:
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
        last_error = "Ответ не был валидным JSON (вероятно, обрыв строки или лишние кавычки)."

    raise GenerationError(
        "AI несколько раз подряд вернул некорректный JSON. Попробуй ещё раз или уменьши число слайдов."
    )


def _has_glued_text(obj, threshold: int = 24) -> bool:
    """
    Обнаруживает баг GigaChat 'словаслипаютсявместе' (текст без пробелов между словами).
    Ищет подозрительно длинные "слова" (токены без пробелов) в любом текстовом поле,
    включая вложенные списки/словари.
    """
    if isinstance(obj, str):
        return any(len(tok) > threshold for tok in obj.split())
    if isinstance(obj, list):
        return any(_has_glued_text(x, threshold) for x in obj)
    if isinstance(obj, dict):
        return any(_has_glued_text(v, threshold) for v in obj.values())
    return False


# ---------------------------------------------------------------------------
# Этап 1: subtopic-заготовки (батчами, чтобы не обрезался ответ)
# ---------------------------------------------------------------------------

async def generate_outline(
    topic: str,
    slide_count: int,
    language: str = "ru",
    detailed_prompt: Optional[str] = None,
) -> List[SlideOutline]:
    lang_instruction = "русском" if language == "ru" else "английском"
    extra = f" Дополнительные требования пользователя: {detailed_prompt.strip()}." if detailed_prompt else ""

    outline: List[SlideOutline] = []
    remaining = slide_count

    while remaining > 0:
        batch_size = min(OUTLINE_BATCH_SIZE, remaining)
        start_index = len(outline) + 1

        def build_prompt(prev_error, batch_size=batch_size, start_index=start_index):
            error_note = f" ВНИМАНИЕ: предыдущая попытка провалилась ({prev_error})." if prev_error else ""
            return (
                f"Ты — эксперт по подготовке университетских презентаций. "
                f"Придумай {batch_size} конкретных содержательных подтем презентации на тему "
                f"\"{topic}\" на {lang_instruction} языке (подтемы с {start_index} по {start_index + batch_size - 1} "
                f"из {slide_count} общих).{extra} "
                "Подтемы не должны повторяться и должны раскрывать тему с разных сторон "
                "(факты, механизмы, сравнения, примеры, контекст). "
                "Используй только реально известные, проверяемые факты — если не уверен в точной "
                "детали (дата, имя, цифра), не выдумывай её, формулируй общее, но правдивое утверждение. "
                "Пиши грамотно: заглавные буквы в начале предложений, пробелы между всеми словами "
                "(никогда не пиши слоставместепробелов). "
                f'Верни строго JSON-массив из {batch_size} объектов вида '
                '{"title": "конкретная подтема", "key_points": ["факт/аргумент 1", "факт/аргумент 2", "факт/аргумент 3"]}. '
                "Не используй кавычки внутри текста вообще. Без markdown, без пояснений, только валидный JSON."
                f"{error_note}"
            )

        items = await _call_gigachat_json(build_prompt, temperature=0.8, max_tokens=2200)
        if not isinstance(items, list):
            raise GenerationError("AI вернул структуру в неожиданном формате.")

        if _has_glued_text(items):
            logger.warning("Обнаружен слипшийся текст в outline-батче, перегенерация")
            items = await _call_gigachat_json(
                lambda _e: build_prompt(None) + " ВАЖНО: в прошлый раз слова слиплись без пробелов — исправь это.",
                temperature=0.6,
                max_tokens=2200,
            )
            if not isinstance(items, list):
                raise GenerationError("AI вернул структуру в неожиданном формате.")

        for item in items[:batch_size]:
            outline.append(
                SlideOutline(title=item.get("title", f"Слайд {len(outline) + 1}"), key_points=item.get("key_points", []))
            )
        remaining = slide_count - len(outline)

    if len(outline) > slide_count:
        outline = outline[:slide_count]
    elif len(outline) < slide_count:
        for _ in range(slide_count - len(outline)):
            outline.append(SlideOutline(title=f"Слайд {len(outline) + 1}", key_points=[]))

    return outline


# ---------------------------------------------------------------------------
# Этап 2: контент под конкретную раскладку слайда
# ---------------------------------------------------------------------------

def _common_header(topic: str, style: str, language: str, detailed_prompt: Optional[str]) -> str:
    lang_instruction = "русском" if language == "ru" else "английском"
    extra = f" Учитывай доп. требования пользователя: {detailed_prompt.strip()}." if detailed_prompt else ""
    return (
        f"Ты готовишь слайд презентации уровня топового университета на тему '{topic}' "
        f"(визуальный стиль '{style}', язык — {lang_instruction}).{extra} "
        "Контент должен быть конкретным, фактурным, без общих расплывчатых фраз и без 'воды'. "
        "ВАЖНО про достоверность: используй только реально известные, проверяемые факты. "
        "Если ты не уверен в точной дате, цифре, имени или детали — НЕ выдумывай её и не указывай "
        "ложную конкретику; вместо этого формулируй мысль более общо, но правдиво. Придумывать "
        "факты (особенно про персонажей, сюжеты, авторов, даты) строго запрещено — лучше меньше "
        "конкретики, чем недостоверная. "
        "ВАЖНО про грамотность: пиши грамотным литературным языком — каждое предложение начинай "
        "с заглавной буквы, разделяй слова пробелами, соблюдай знаки препинания. Никогда не пиши "
        "слоставместепробелов — между каждым словом обязателен пробел."
    )


def _notes_field(with_notes: bool) -> str:
    return (
        '"speaker_notes": "2-3 содержательных предложения для докладчика"'
        if with_notes
        else '"speaker_notes": ""'
    )


LAYOUT_SCHEMAS = {
    LAYOUT_TITLE: lambda notes_f: (
        '{"subtitle": "короткий информативный подзаголовок (до 12 слов)", ' + notes_f + "}"
    ),
    LAYOUT_TOC: lambda notes_f: (
        '{"items": ["пункт оглавления 1", "пункт оглавления 2", "..."], ' + notes_f + "}"
    ),
    LAYOUT_THESIS_PROOF: lambda notes_f: (
        '{"claim": "готовое утверждение-вывод (не название темы, а законченная мысль)", '
        '"facts": ["факт-подтверждение 1", "факт-подтверждение 2", "факт-подтверждение 3"], '
        '"image_keywords": ["visual keyword 1", "visual keyword 2"], ' + notes_f + "}"
    ),
    LAYOUT_DENSE_TEXT: lambda notes_f: (
        '{"problem": "заголовок, обозначающий проблему/вопрос", '
        '"paragraph": "объёмный содержательный абзац текста (5-8 предложений) по теме", '
        '"image_keywords": ["visual keyword 1", "visual keyword 2"], ' + notes_f + "}"
    ),
    LAYOUT_CONCEPT_ANATOMY: lambda notes_f: (
        '{"term": "название термина/понятия", '
        '"definition": "ёмкое академическое определение термина (2-3 предложения)", '
        '"parts": [{"name": "название части/элемента", "function": "её функция одной строкой"}, '
        '{"name": "...", "function": "..."}, {"name": "...", "function": "..."}], '
        '"image_keywords": ["visual keyword 1", "visual keyword 2"], ' + notes_f + "}"
    ),
    LAYOUT_COMPARISON: lambda notes_f: (
        '{"summary": "аналитический заголовок, резюмирующий итог сравнения", '
        '"left_label": "название объекта 1", "right_label": "название объекта 2", '
        '"left_points": ["параметр сравнения 1", "параметр сравнения 2", "параметр сравнения 3"], '
        '"right_points": ["параметр сравнения 1 (в том же порядке что и слева)", "параметр 2", "параметр 3"], '
        '"image_keywords": ["visual keyword 1", "visual keyword 2"], ' + notes_f + "}"
    ),
    LAYOUT_CAUSAL_CHAIN: lambda notes_f: (
        '{"process_title": "заголовок, объясняющий суть явления (например Почему происходит Х)", '
        '"cause": "причина (ёмкий текст)", "mechanism": "механизм действия (более глубокий текст)", '
        '"effect": "следствие/итог (финальный вывод)", '
        '"image_keywords": ["visual keyword 1", "visual keyword 2"], ' + notes_f + "}"
    ),
    LAYOUT_QUOTE_CONTEXT: lambda notes_f: (
        '{"context": "короткий вводный контекст (1-2 предложения)", '
        '"quote": "ключевая цитата или выдержка по теме (без кавычек в самом тексте, кавычки добавит вёрстка)", '
        '"explanation": "глубокий комментарий-пояснение: что это значит (3-4 предложения)", '
        '"image_keywords": ["portrait/context visual keyword 1", "keyword 2"], ' + notes_f + "}"
    ),
    LAYOUT_CONCLUSION: lambda notes_f: (
        '{"bullets": ["ключевой вывод 1", "ключевой вывод 2", "ключевой вывод 3"], ' + notes_f + "}"
    ),
}


async def generate_layout_content(
    topic: str,
    outline_item: SlideOutline,
    layout: str,
    style: str,
    language: str = "ru",
    with_notes: bool = False,
    detailed_prompt: Optional[str] = None,
) -> Dict:
    """Генерирует контент слайда строго под поля указанной раскладки (layout)."""
    header = _common_header(topic, style, language, detailed_prompt)
    schema = LAYOUT_SCHEMAS.get(layout, LAYOUT_SCHEMAS[LAYOUT_THESIS_PROOF])(_notes_field(with_notes))
    subtopic_hint = f"Подтема слайда: '{outline_item.title}'. Опорные факты: {outline_item.key_points}."

    def build_prompt(prev_error):
        error_note = f" ВНИМАНИЕ: предыдущая попытка провалилась ({prev_error}). Верни строго валидный JSON." if prev_error else ""
        image_note = (
            " Если в схеме есть поле image_keywords: указывай 2-3 конкретных ВИЗУАЛЬНЫХ слова "
            "НА АНГЛИЙСКОМ языке для поиска стокового фото (например 'ocean coral reef' или "
            "'business meeting handshake'). НЕ используй имена собственные, названия брендов, "
            "персонажей мультфильмов/фильмов или конкретных людей — на стоках таких фото нет, "
            "результат будет случайным. Вместо имени персонажа опиши визуальную суть сцены "
            "(например вместо 'SpongeBob' — 'cartoon underwater sea sponge character')."
            if "image_keywords" in schema else ""
        )
        return (
            f"{header} {subtopic_hint} "
            f'Верни строго JSON-объект вида {schema}. '
            "Не используй кавычки внутри текстовых значений вообще. Без markdown, только валидный JSON."
            f"{image_note}{error_note}"
        )

    item = await _call_gigachat_json(build_prompt, temperature=0.7, max_tokens=1500)
    if not isinstance(item, dict):
        raise GenerationError("AI вернул слайд в неожиданном формате.")

    # Если модель "слепила" слова без пробелов — пробуем перегенерировать пару раз,
    # явно указав на проблему, прежде чем смириться с текущим результатом.
    glued_retries = 0
    while _has_glued_text(item) and glued_retries < 2:
        glued_retries += 1
        logger.warning("Обнаружен слипшийся текст в слайде, перегенерация (попытка %d)", glued_retries)

        def build_prompt_glued(prev_error, _schema=schema, _hint=subtopic_hint):
            return (
                f"{header} {_hint} "
                "ПРЕДЫДУЩИЙ ОТВЕТ СОДЕРЖАЛ СЛОВА БЕЗ ПРОБЕЛОВ МЕЖДУ НИМИ (например "
                "'словослиплосьвместе') — это критическая ошибка. Перепиши текст заново, "
                "обязательно ставь пробел между КАЖДОЙ парой слов. "
                f'Верни строго JSON-объект вида {_schema}. '
                "Не используй кавычки внутри текстовых значений вообще. Без markdown, только валидный JSON."
            )

        item = await _call_gigachat_json(build_prompt_glued, temperature=0.5, max_tokens=1500)
        if not isinstance(item, dict):
            raise GenerationError("AI вернул слайд в неожиданном формате.")

    item.setdefault("image_keywords", [])
    item["image_keywords"] = (item.get("image_keywords") or [])[:3]
    item.setdefault("speaker_notes", None)
    item["title"] = outline_item.title
    item["layout"] = layout
    return item
