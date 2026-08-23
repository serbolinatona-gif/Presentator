"""
slidev_builder.py
Генерация самодостаточного HTML-превью презентации (без Node/Slidev-тулчейна) —
показывается во встроенном iframe. Отражает те же 9 раскладок и 5 стилей, что и
pptx_builder.py: картинка размещается РЯДОМ с текстом, а не фоном на весь слайд.
"""

from typing import List

STYLE_CSS = {
    "minimal": {"bg": "#F9F9F9", "title": "#1A1A1A", "text": "#333333", "accent": "#1D4ED8", "muted": "#9CA3AF", "font": "Helvetica, Arial, sans-serif"},
    "academic": {"bg": "#FAF9F6", "title": "#1D3557", "text": "#222222", "accent": "#1D3557", "muted": "#6B7280", "font": "Georgia, 'Times New Roman', serif"},
    "creative": {"bg": "#FFFFFF", "title": "#18122B", "text": "#3D3355", "accent": "#6C5CE7", "muted": "#9B93B8", "font": "Verdana, sans-serif",
                 "palette": ["#FF6B6B", "#4ECDC4", "#FFD93D", "#6C5CE7", "#1DD3B0", "#FF9F43", "#3AB0FF", "#F368E0"]},
    "corporate": {"bg": "#FFFFFF", "title": "#0F172A", "text": "#1E293B", "accent": "#0EA5E9", "muted": "#64748B", "font": "Calibri, Arial, sans-serif"},
    "dark": {"bg": "#121212", "title": "#FFFFFF", "text": "#E0E0E0", "accent": "#22D3EE", "muted": "#9CA3AF", "font": "Calibri, Arial, sans-serif"},
}


import re


def _clean_text(text) -> str:
    """Та же логика, что и в pptx_builder.py: схлопывание пробелов/переносов + капитализация."""
    if not text:
        return ""
    s = str(text).replace("\r", " ").replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    if s and s[0].isalpha() and s[0].islower():
        s = s[0].upper() + s[1:]
    return s


def _esc(text) -> str:
    if text is None:
        return ""
    text = _clean_text(text)
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _as_list(value):
    """Защита: если AI вернул строку вместо списка, не разбиваем её по буквам."""
    if isinstance(value, str):
        return [value] if value else []
    return value or []


def _img_tag(image_url, css_class="slide-img"):
    if not image_url:
        return ""
    return f'<div class="{css_class}" style="background-image:url(\'{_esc(image_url)}\')"></div>'


def _render_body(layout: str, slide: dict, c: dict) -> str:
    if layout == "title":
        sub = f'<p class="subtitle">{_esc(slide.get("subtitle"))}</p>' if slide.get("subtitle") else ""
        return f'<h1 class="title-big">{_esc(slide.get("title"))}</h1>{sub}'

    if layout == "toc":
        items = slide.get("items") or []
        rows = "".join(f'<li><span class="num">{i+1:02d}</span>{_esc(x)}</li>' for i, x in enumerate(items))
        return f'<h2>{_esc(slide.get("title"))}</h2><ol class="toc-list">{rows}</ol>'

    if layout == "thesis_proof":
        facts = "".join(f"<li>{_esc(x)}</li>" for x in (slide.get("facts") or []))
        return f'''
        <div class="split">
          <div class="col">
            <h2 class="claim">{_esc(slide.get("claim") or slide.get("title"))}</h2>
            <ul class="facts">{facts}</ul>
          </div>
          <div class="col">{_img_tag(slide.get("image_url"), "side-img")}</div>
        </div>'''

    if layout == "dense_text":
        img = _img_tag(slide.get("image_url"), "side-img-small") if slide.get("image_url") else ""
        return f'''
        <h2>{_esc(slide.get("problem") or slide.get("title"))}</h2>
        <div class="split">
          <p class="paragraph">{_esc(slide.get("paragraph"))}</p>
          {img}
        </div>'''

    if layout == "concept_anatomy":
        parts = "".join(
            f'<li><b>{_esc(p.get("name") if isinstance(p, dict) else p)}</b> — '
            f'{_esc(p.get("function") if isinstance(p, dict) else "")}</li>'
            for p in (slide.get("parts") or [])
        )
        return f'''
        <div class="split">
          <div class="col">
            <h2>{_esc(slide.get("term") or slide.get("title"))}</h2>
            <p class="definition">{_esc(slide.get("definition"))}</p>
            <ul class="parts">{parts}</ul>
          </div>
          <div class="col">{_img_tag(slide.get("image_url"), "side-img")}</div>
        </div>'''

    if layout == "comparison":
        left = "".join(f"<li>{_esc(x)}</li>" for x in (slide.get("left_points") or []))
        right = "".join(f"<li>{_esc(x)}</li>" for x in (slide.get("right_points") or []))
        img = _img_tag(slide.get("image_url"), "band-img") if slide.get("image_url") else ""
        return f'''
        <h2>{_esc(slide.get("summary") or slide.get("title"))}</h2>
        {img}
        <div class="split divider">
          <div class="col"><h3>{_esc(slide.get("left_label"))}</h3><ul>{left}</ul></div>
          <div class="col"><h3>{_esc(slide.get("right_label"))}</h3><ul>{right}</ul></div>
        </div>'''

    if layout == "causal_chain":
        cards = "".join(
            f'<div class="chain-card"><span class="chain-label">{lbl}</span><p>{_esc(val)}</p></div>'
            + ("<span class=\"arrow\">&rarr;</span>" if i < 2 else "")
            for i, (lbl, val) in enumerate([
                ("ПРИЧИНА", slide.get("cause")),
                ("МЕХАНИЗМ", slide.get("mechanism")),
                ("ИТОГ", slide.get("effect")),
            ])
        )
        img = _img_tag(slide.get("image_url"), "band-img") if slide.get("image_url") else ""
        return f'<h2>{_esc(slide.get("process_title") or slide.get("title"))}</h2><div class="chain">{cards}</div>{img}'

    if layout == "quote_context":
        return f'''
        <div class="split">
          <div class="col">{_img_tag(slide.get("image_url"), "side-img")}</div>
          <div class="col">
            <p class="context">{_esc(slide.get("context"))}</p>
            <p class="quote">&laquo;{_esc(slide.get("quote"))}&raquo;</p>
            <p class="explanation">{_esc(slide.get("explanation"))}</p>
          </div>
        </div>'''

    if layout == "conclusion":
        bullets = "".join(f"<li>&#10003; {_esc(x)}</li>" for x in (slide.get("bullets") or []))
        return f'<h2 class="title-big" style="font-size:2rem">{_esc(slide.get("title"))}</h2><ul class="conclusion-list">{bullets}</ul>'

    # fallback
    return f'<h2>{_esc(slide.get("title"))}</h2>'


def _creative_decor(idx: int, c: dict) -> str:
    """Разноцветные декоративные фигуры для креативного стиля (аналог pptx draw_style_frame)."""
    palette = c.get("palette") or [c["accent"]]

    def pcolor(offset):
        return palette[(idx + offset) % len(palette)]

    return f'''
    <div class="creative-shape shape-circle-lg" style="background:{pcolor(0)}"></div>
    <div class="creative-shape shape-circle-sm" style="background:{pcolor(1)}"></div>
    <div class="creative-shape shape-square" style="background:{pcolor(2)}"></div>
    <div class="creative-shape shape-triangle" style="border-bottom-color:{pcolor(3)}"></div>
    '''


def _render_slide(idx: int, slide: dict, c: dict, style: str) -> str:
    layout = slide.get("layout", "thesis_proof")
    body = _render_body(layout, slide, c)
    decor = _creative_decor(idx, c) if style == "creative" else ""
    return f'<section class="slide layout-{layout}">{decor}<div class="slide-inner">{body}</div></section>'


def build_html_preview(presentation_title: str, slides: List[dict], style: str = "minimal", language: str = "ru") -> str:
    c = STYLE_CSS.get(style, STYLE_CSS["minimal"])
    slides_html = "".join(_render_slide(i, s, c, style) for i, s in enumerate(slides))
    total = len(slides)
    nav_prev = "Назад" if language == "ru" else "Prev"
    nav_next = "Далее" if language == "ru" else "Next"

    return f"""<!DOCTYPE html>
<html lang="{language}">
<head>
<meta charset="UTF-8" />
<title>{_esc(presentation_title)}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ height: 100%; overflow: hidden; font-family: {c['font']}; }}
  .deck {{ position: relative; width: 100%; height: 100vh; background: {c['bg']}; }}
  .slide {{ position: absolute; inset: 0; display: none; padding: 4.5% 5%; }}
  .slide.active {{ display: flex; flex-direction: column; justify-content: center; }}
  .slide-inner {{ width: 100%; animation: fadeUp 0.5s ease both; }}
  h1, h2, h3 {{ color: {c['title']}; }}
  h1.title-big {{ font-size: 2.6rem; font-weight: 800; text-align: center; }}
  .subtitle {{ text-align: center; color: {c['text']}; font-size: 1.15rem; margin-top: 1rem; }}
  h2 {{ font-size: 1.7rem; font-weight: 700; margin-bottom: 0.9rem; }}
  h3 {{ font-size: 1.1rem; color: {c['accent']}; margin-bottom: 0.6rem; }}
  p, li {{ color: {c['text']}; font-size: 1rem; line-height: 1.55; }}
  .split {{ display: flex; gap: 2.2rem; align-items: stretch; flex: 1; }}
  .split .col {{ flex: 1; display: flex; flex-direction: column; }}
  .split.divider .col:first-child {{ border-right: 1px solid {c['muted']}; padding-right: 1.5rem; }}
  .side-img {{ flex: 1; border-radius: 10px; background-size: cover; background-position: center; min-height: 220px; }}
  .side-img-small {{ width: 38%; border-radius: 10px; background-size: cover; background-position: center; }}
  .band-img {{ height: 130px; border-radius: 10px; background-size: cover; background-position: center; margin: 0.8rem 0; }}
  ul {{ list-style: none; }}
  ul.facts li, ul.parts li {{ margin-bottom: 0.7rem; padding-left: 1.1rem; position: relative; }}
  ul.facts li::before {{ content: "\\2014"; position: absolute; left: 0; color: {c['accent']}; }}
  ul.parts li b {{ color: {c['accent']}; }}
  .toc-list li {{ display: flex; align-items: center; gap: 0.8rem; padding: 0.5rem 0; font-size: 1.1rem; }}
  .toc-list .num {{ color: {c['accent']}; font-weight: 800; width: 2rem; }}
  .claim {{ font-size: 1.5rem; margin-bottom: 1rem; line-height: 1.3; }}
  .definition {{ margin-bottom: 1rem; }}
  .paragraph {{ line-height: 1.6; }}
  .chain {{ display: flex; align-items: center; gap: 0.8rem; margin-top: 1rem; }}
  .chain-card {{ flex: 1; background: rgba(127,127,127,0.08); border: 1px solid {c['muted']}; border-radius: 10px; padding: 1rem; }}
  .chain-label {{ color: {c['accent']}; font-weight: 800; font-size: 0.8rem; }}
  .arrow {{ font-size: 1.6rem; color: {c['accent']}; }}
  .context {{ color: {c['muted']}; font-style: italic; margin-bottom: 0.8rem; }}
  .quote {{ font-size: 1.4rem; font-weight: 800; color: {c['title']}; margin-bottom: 1rem; line-height: 1.3; }}
  .conclusion-list {{ max-width: 700px; margin: 1.5rem auto 0; }}
  .conclusion-list li {{ font-size: 1.15rem; margin-bottom: 0.9rem; }}
  .creative-shape {{ position: absolute; border-radius: 50%; opacity: 0.85; z-index: 0; pointer-events: none; }}
  .shape-circle-lg {{ width: 220px; height: 220px; top: -90px; right: -60px; opacity: 0.5; }}
  .shape-circle-sm {{ width: 60px; height: 60px; top: 110px; right: 60px; }}
  .shape-square {{ width: 130px; height: 130px; bottom: -50px; left: -40px; border-radius: 24px; transform: rotate(20deg); opacity: 0.55; }}
  .shape-triangle {{
    position: absolute; bottom: 40px; left: 30px; width: 0; height: 0;
    border-left: 30px solid transparent; border-right: 30px solid transparent;
    border-bottom: 52px solid; transform: rotate(-15deg); border-radius: 0;
  }}
  .slide-inner {{ position: relative; z-index: 1; }}
  .nav {{
    position: fixed; bottom: 16px; left: 50%; transform: translateX(-50%);
    display: flex; gap: 10px; align-items: center;
    background: rgba(0,0,0,0.55); padding: 8px 16px; border-radius: 999px;
    font-size: 0.85rem; color: #fff; z-index: 10;
  }}
  .nav button {{ background: {c['accent']}; border: none; color: #111; padding: 6px 14px; border-radius: 999px; cursor: pointer; font-weight: 600; }}
  .nav button:disabled {{ opacity: 0.4; cursor: default; }}
  @keyframes fadeUp {{ from {{ opacity: 0; transform: translateY(14px); }} to {{ opacity: 1; transform: translateY(0); }} }}
</style>
</head>
<body>
<div class="deck" id="deck">{slides_html}</div>
<div class="nav">
  <button id="prevBtn">{nav_prev}</button>
  <span id="counter">1 / {total}</span>
  <button id="nextBtn">{nav_next}</button>
</div>
<script>
  const slides = document.querySelectorAll('.slide');
  let current = 0;
  function show(i) {{
    slides.forEach((s, idx) => s.classList.toggle('active', idx === i));
    document.getElementById('counter').textContent = (i + 1) + ' / ' + slides.length;
    document.getElementById('prevBtn').disabled = i === 0;
    document.getElementById('nextBtn').disabled = i === slides.length - 1;
  }}
  document.getElementById('prevBtn').onclick = () => {{ if (current > 0) show(--current); }};
  document.getElementById('nextBtn').onclick = () => {{ if (current < slides.length - 1) show(++current); }};
  document.addEventListener('keydown', (e) => {{
    if (e.key === 'ArrowRight') document.getElementById('nextBtn').click();
    if (e.key === 'ArrowLeft') document.getElementById('prevBtn').click();
  }});
  let touchStartX = 0;
  document.addEventListener('touchstart', e => touchStartX = e.touches[0].clientX);
  document.addEventListener('touchend', e => {{
    const dx = e.changedTouches[0].clientX - touchStartX;
    if (dx > 50) document.getElementById('prevBtn').click();
    if (dx < -50) document.getElementById('nextBtn').click();
  }});

  // Автоподгонка: если контент слайда не помещается по высоте — плавно уменьшаем
  // шрифт всего слайда, пока не влезет (защита от переполнения на разных экранах).
  function fitSlide(slide) {{
    const inner = slide.querySelector('.slide-inner');
    if (!inner) return;
    let fontScale = 100;
    let guard = 0;
    while (inner.scrollHeight > slide.clientHeight && fontScale > 55 && guard < 20) {{
      fontScale -= 5;
      inner.style.fontSize = fontScale + '%';
      guard++;
    }}
  }}
  function fitAllSlides() {{
    slides.forEach(s => {{
      const wasActive = s.classList.contains('active');
      s.classList.add('active');
      s.style.visibility = 'hidden';
      fitSlide(s);
      s.style.visibility = '';
      if (!wasActive) s.classList.remove('active');
    }});
  }}
  window.addEventListener('load', fitAllSlides);
  window.addEventListener('resize', fitAllSlides);

  show(0);
</script>
</body>
</html>"""
