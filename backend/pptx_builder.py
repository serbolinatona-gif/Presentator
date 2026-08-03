"""
pptx_builder.py
Сборка готового .pptx файла из сгенерированного контента слайдов,
с фоновыми изображениями (или градиентной заглушкой) и РАЗНОЙ вёрсткой под каждый
из 5 стилей — как в реальных профессиональных шаблонах, а не один и тот же макет.

Исправленный баг: слайд.fill.transparency НЕ существует в публичном API python-pptx
(это не задокументированное свойство — при вызове бросало AttributeError, из-за чего
сборка презентации падала в середине, и пользователь получал pptx без стилей/картинок,
собранный из fallback-кода). Прозрачность теперь выставляется вручную через прямую
работу с XML (oxml), как это единственно возможно сделать в python-pptx.
"""

import io
import logging
from typing import List, Optional

import httpx
from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

logger = logging.getLogger("slideforge.pptx_builder")

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)

STYLE_PRESETS = {
    "minimal": {
        "bg": "FFFFFF", "title": "111111", "text": "333333",
        "accent": "6366F1", "font_title": "Helvetica", "font_body": "Helvetica",
    },
    "academic": {
        "bg": "F7F5F0", "title": "1F2937", "text": "374151",
        "accent": "1D4ED8", "font_title": "Georgia", "font_body": "Georgia",
    },
    "creative": {
        "bg": "FDF2F8", "title": "831843", "text": "4B1D3F",
        "accent": "EC4899", "font_title": "Verdana", "font_body": "Verdana",
    },
    "corporate": {
        "bg": "0F172A", "title": "0F172A", "text": "1E293B",
        "accent": "0EA5E9", "font_title": "Calibri", "font_body": "Calibri",
    },
    "dark": {
        "bg": "0F0F14", "title": "FFFFFF", "text": "D1D5DB",
        "accent": "8B5CF6", "font_title": "Helvetica", "font_body": "Helvetica",
    },
}


def _hex_to_rgb(hex_color: str) -> RGBColor:
    return RGBColor.from_string(hex_color.lstrip("#"))


def _set_shape_transparency(shape, alpha_percent: float):
    """
    Прозрачность заливки через прямую правку XML — единственный рабочий способ
    в python-pptx (нет публичного API вроде shape.fill.transparency).
    alpha_percent: 0.0 (полностью прозрачно) .. 1.0 (полностью непрозрачно)
    """
    sp = shape.fill.fore_color._xFill
    srgb = sp.find("{http://schemas.openxmlformats.org/drawingml/2006/main}srgbClr")
    if srgb is None:
        return
    alpha_val = str(int(alpha_percent * 100000))
    alpha_el = etree.SubElement(
        srgb, "{http://schemas.openxmlformats.org/drawingml/2006/main}alpha"
    )
    alpha_el.set("val", alpha_val)


async def _download_image(url: str) -> Optional[bytes]:
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, follow_redirects=True)
            if resp.status_code == 200:
                return resp.content
    except httpx.RequestError as exc:
        logger.warning("Не удалось скачать изображение %s: %s", url, exc)
    return None


def _add_gradient_background(slide, color1: str, color2: str, angle: float = 45.0):
    fill = slide.background.fill
    fill.gradient()
    stops = fill.gradient_stops
    stops[0].color.rgb = _hex_to_rgb(color1)
    stops[1].color.rgb = _hex_to_rgb(color2)
    fill.gradient_angle = angle


def _add_solid_background(slide, color_hex: str):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = _hex_to_rgb(color_hex)


def _add_title(slide, text, left, top, width, height, size, color, font, bold=True, align=PP_ALIGN.LEFT):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(size)
    p.font.bold = bold
    p.font.name = font
    p.font.color.rgb = _hex_to_rgb(color)
    p.alignment = align
    return box


def _add_bullets(slide, bullets, left, top, width, height, size, color, font, marker="•  "):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    for i, bullet in enumerate(bullets):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = f"{marker}{bullet}"
        p.font.size = Pt(size)
        p.font.name = font
        p.font.color.rgb = _hex_to_rgb(color)
        p.space_after = Pt(12)
    return box


def _add_accent_bar(slide, left, top, width, height, color_hex):
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    bar.fill.solid()
    bar.fill.fore_color.rgb = _hex_to_rgb(color_hex)
    bar.line.fill.background()
    return bar


def _add_image_with_overlay(slide, image_bytes, overlay_top, overlay_height, overlay_alpha=0.4):
    slide.shapes.add_picture(io.BytesIO(image_bytes), 0, 0, width=SLIDE_W, height=SLIDE_H)
    overlay = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, overlay_top, SLIDE_W, overlay_height)
    overlay.fill.solid()
    overlay.fill.fore_color.rgb = _hex_to_rgb("000000")
    overlay.line.fill.background()
    _set_shape_transparency(overlay, overlay_alpha)
    return overlay


def _render_minimal(slide, slide_data, preset, is_title, idx):
    _add_solid_background(slide, preset["bg"])
    if is_title:
        _add_title(slide, slide_data["title"], Inches(1.0), Inches(3.0), Inches(11.3), Inches(1.3),
                    44, preset["title"], preset["font_title"], align=PP_ALIGN.CENTER)
        if slide_data.get("subtitle") or slide_data.get("bullets"):
            sub = slide_data.get("subtitle") or slide_data["bullets"][0]
            _add_title(slide, sub, Inches(1.5), Inches(4.3), Inches(10.3), Inches(0.8),
                        18, preset["text"], preset["font_body"], bold=False, align=PP_ALIGN.CENTER)
        return
    _add_accent_bar(slide, Inches(0.7), Inches(0.75), Inches(0.5), Inches(0.08), preset["accent"])
    _add_title(slide, slide_data["title"], Inches(0.7), Inches(0.95), Inches(11.9), Inches(1.1),
                30, preset["title"], preset["font_title"])
    if slide_data.get("bullets"):
        _add_bullets(slide, slide_data["bullets"], Inches(0.7), Inches(2.3), Inches(11.9), Inches(4.5),
                      18, preset["text"], preset["font_body"])


def _render_academic(slide, slide_data, preset, is_title, idx):
    _add_solid_background(slide, preset["bg"])
    _add_accent_bar(slide, 0, 0, SLIDE_W, Inches(0.15), preset["accent"])
    if is_title:
        _add_title(slide, slide_data["title"], Inches(1.0), Inches(2.8), Inches(11.3), Inches(1.3),
                    40, preset["title"], preset["font_title"], align=PP_ALIGN.CENTER)
        sub = slide_data.get("subtitle") or (slide_data["bullets"][0] if slide_data.get("bullets") else "")
        if sub:
            _add_title(slide, sub, Inches(1.5), Inches(4.0), Inches(10.3), Inches(0.8),
                        18, preset["text"], preset["font_body"], bold=False, align=PP_ALIGN.CENTER)
        return
    _add_title(slide, f"{idx:02d}", Inches(0.6), Inches(0.5), Inches(1.2), Inches(0.7),
                20, preset["accent"], preset["font_title"])
    _add_title(slide, slide_data["title"], Inches(1.6), Inches(0.5), Inches(10.9), Inches(1.0),
                28, preset["title"], preset["font_title"])
    if slide_data.get("bullets"):
        _add_bullets(slide, slide_data["bullets"], Inches(1.6), Inches(1.8), Inches(11.0), Inches(4.8),
                      17, preset["text"], preset["font_body"], marker="— ")


def _render_creative(slide, slide_data, preset, is_title, idx):
    _add_gradient_background(slide, preset["bg"], preset["accent"], angle=135.0 if idx % 2 else 45.0)
    # декоративная геометрия
    circle = slide.shapes.add_shape(MSO_SHAPE.OVAL, SLIDE_W - Inches(3.2), Inches(-1.2), Inches(4.2), Inches(4.2))
    circle.fill.solid()
    circle.fill.fore_color.rgb = _hex_to_rgb("FFFFFF")
    circle.line.fill.background()
    _set_shape_transparency(circle, 0.12)

    triangle = slide.shapes.add_shape(MSO_SHAPE.ISOCELES_TRIANGLE, Inches(-1.0), SLIDE_H - Inches(2.2), Inches(3.2), Inches(3.2))
    triangle.rotation = 15
    triangle.fill.solid()
    triangle.fill.fore_color.rgb = _hex_to_rgb("FFFFFF")
    triangle.line.fill.background()
    _set_shape_transparency(triangle, 0.1)

    title_color = "FFFFFF"
    if is_title:
        _add_title(slide, slide_data["title"], Inches(1.0), Inches(2.9), Inches(11.3), Inches(1.4),
                    46, title_color, preset["font_title"], align=PP_ALIGN.CENTER)
        sub = slide_data.get("subtitle") or (slide_data["bullets"][0] if slide_data.get("bullets") else "")
        if sub:
            _add_title(slide, sub, Inches(1.5), Inches(4.2), Inches(10.3), Inches(0.8),
                        19, "F5D0E7", preset["font_body"], bold=False, align=PP_ALIGN.CENTER)
        return
    _add_title(slide, slide_data["title"], Inches(0.8), Inches(0.8), Inches(11.7), Inches(1.2),
                32, title_color, preset["font_title"])
    if slide_data.get("bullets"):
        _add_bullets(slide, slide_data["bullets"], Inches(0.8), Inches(2.2), Inches(10.8), Inches(4.5),
                      19, "FCE7F3", preset["font_body"], marker="◆  ")


def _render_corporate(slide, slide_data, preset, is_title, idx):
    _add_solid_background(slide, "FFFFFF")
    sidebar_w = Inches(4.2)
    sidebar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, sidebar_w, SLIDE_H)
    sidebar.fill.solid()
    sidebar.fill.fore_color.rgb = _hex_to_rgb("0F172A")
    sidebar.line.fill.background()
    _add_accent_bar(slide, 0, Inches(0.6), Inches(0.55), Inches(0.08), preset["accent"])

    if is_title:
        _add_title(slide, slide_data["title"], Inches(0.5), Inches(2.7), sidebar_w - Inches(1.0), Inches(2.0),
                    32, "FFFFFF", preset["font_title"])
        sub = slide_data.get("subtitle") or (slide_data["bullets"][0] if slide_data.get("bullets") else "")
        if sub:
            _add_title(slide, sub, Inches(0.5), Inches(4.5), sidebar_w - Inches(1.0), Inches(1.5),
                        15, "94A3B8", preset["font_body"], bold=False)
        return

    _add_title(slide, f"{idx:02d}", Inches(0.5), Inches(0.5), Inches(2.0), Inches(0.6),
                16, preset["accent"], preset["font_title"])
    _add_title(slide, slide_data["title"], Inches(0.5), Inches(1.1), sidebar_w - Inches(1.0), Inches(2.2),
                24, "FFFFFF", preset["font_title"])
    if slide_data.get("bullets"):
        _add_bullets(slide, slide_data["bullets"], sidebar_w + Inches(0.6), Inches(0.9), Inches(8.0), Inches(5.5),
                      18, "1E293B", preset["font_body"])


def _render_dark(slide, slide_data, preset, is_title, idx):
    _add_solid_background(slide, preset["bg"])
    if is_title:
        _add_title(slide, slide_data["title"], Inches(1.0), Inches(3.0), Inches(11.3), Inches(1.3),
                    44, "FFFFFF", preset["font_title"], align=PP_ALIGN.CENTER)
        sub = slide_data.get("subtitle") or (slide_data["bullets"][0] if slide_data.get("bullets") else "")
        if sub:
            _add_title(slide, sub, Inches(1.5), Inches(4.3), Inches(10.3), Inches(0.8),
                        18, "9CA3AF", preset["font_body"], bold=False, align=PP_ALIGN.CENTER)
        return
    _add_accent_bar(slide, Inches(0.7), Inches(0.8), Inches(0.5), Inches(0.08), preset["accent"])
    _add_title(slide, slide_data["title"], Inches(0.7), Inches(1.0), Inches(11.9), Inches(1.1),
                30, "FFFFFF", preset["font_title"])
    if slide_data.get("bullets"):
        _add_bullets(slide, slide_data["bullets"], Inches(0.7), Inches(2.3), Inches(11.9), Inches(4.5),
                      18, preset["text"] if preset["text"] != "1E293B" else "D1D5DB", preset["font_body"])


STYLE_RENDERERS = {
    "minimal": _render_minimal,
    "academic": _render_academic,
    "creative": _render_creative,
    "corporate": _render_corporate,
    "dark": _render_dark,
}


async def build_pptx(
    title: str,
    slides: List[dict],
    style: str = "minimal",
    language: str = "ru",
) -> bytes:
    """
    slides: список dict вида
        {"title": str, "subtitle": Optional[str], "bullets": [str],
         "image_url": Optional[str], "notes": Optional[str]}
    Возвращает bytes готового .pptx файла.
    """
    preset = STYLE_PRESETS.get(style, STYLE_PRESETS["minimal"])
    renderer = STYLE_RENDERERS.get(style, _render_minimal)

    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    blank_layout = prs.slide_layouts[6]

    for idx, slide_data in enumerate(slides):
        slide = prs.slides.add_slide(blank_layout)
        is_title_slide = idx == 0

        # Фоновое изображение — только для "не-титульных" слайдов с картинкой,
        # чтобы стиль-специфичная вёрстка (сайдбар, геометрия) не терялась.
        image_bytes = None
        use_full_bg_image = style in ("minimal", "dark") and slide_data.get("image_url") and not is_title_slide
        if use_full_bg_image:
            image_bytes = await _download_image(slide_data["image_url"])

        try:
            if image_bytes:
                _add_image_with_overlay(slide, image_bytes, Inches(4.4), Inches(3.1), overlay_alpha=0.55)
                # Текст поверх фото рисуем отдельно (светлый), а не через renderer с preset-цветами
                _add_title(slide, slide_data["title"], Inches(0.7), Inches(4.6), Inches(11.9), Inches(1.1),
                            30, "FFFFFF", preset["font_title"])
                if slide_data.get("bullets"):
                    _add_bullets(slide, slide_data["bullets"][:3], Inches(0.7), Inches(5.7), Inches(11.9), Inches(1.6),
                                  16, "F3F4F6", preset["font_body"])
            else:
                renderer(slide, slide_data, preset, is_title_slide, idx)
        except Exception:
            logger.exception("Ошибка рендера слайда %d стилем %s — рисуем безопасный fallback", idx, style)
            _add_solid_background(slide, preset["bg"])
            _add_title(slide, slide_data.get("title", ""), Inches(0.7), Inches(0.8), Inches(11.9), Inches(1.1),
                        28, preset["title"], preset["font_title"])
            if slide_data.get("bullets"):
                _add_bullets(slide, slide_data["bullets"], Inches(0.7), Inches(2.1), Inches(11.9), Inches(4.5),
                              18, preset["text"], preset["font_body"])

        if slide_data.get("notes"):
            slide.notes_slide.notes_text_frame.text = slide_data["notes"]

        if not is_title_slide:
            num_box = slide.shapes.add_textbox(SLIDE_W - Inches(1.0), SLIDE_H - Inches(0.5), Inches(0.8), Inches(0.4))
            np_ = num_box.text_frame.paragraphs[0]
            np_.text = str(idx + 1)
            np_.font.size = Pt(11)
            np_.font.color.rgb = _hex_to_rgb(
                "94A3B8" if style in ("dark", "corporate") else "9CA3AF"
            )

    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()
