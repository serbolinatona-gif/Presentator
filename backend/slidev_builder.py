"""
slidev_builder.py

Примечание: полноценная сборка через Slidev требует отдельного Node.js
build-пайплайна (CLI, Vue, отдельный dev-сервер) и плохо ложится в один
бесплатный веб-сервис вместе с FastAPI-бэкендом. Поэтому здесь генерируется
самодостаточный HTML-файл с тем же визуальным результатом: полноэкранные
слайды, стрелки/свайп/клавиатура для навигации, плавные CSS-анимации
появления и фоновые изображения — без необходимости в Node на сервере.
Этот HTML отдаётся во фронтенд и показывается во встроенном iframe.
"""

from typing import List, Optional

STYLE_CSS = {
    "minimal": {"bg": "#ffffff", "title": "#111111", "text": "#333333", "accent": "#6366f1"},
    "academic": {"bg": "#f7f5f0", "title": "#1f2937", "text": "#374151", "accent": "#1d4ed8"},
    "creative": {"bg": "#fdf2f8", "title": "#831843", "text": "#4b1d3f", "accent": "#ec4899"},
    "corporate": {"bg": "#ffffff", "title": "#0f172a", "text": "#1e293b", "accent": "#0ea5e9"},
    "dark": {"bg": "#0f0f14", "title": "#ffffff", "text": "#d1d5db", "accent": "#8b5cf6"},
}


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_slide(idx: int, slide: dict, colors: dict) -> str:
    title = _escape(slide["title"])
    subtitle = slide.get("subtitle")
    bullets = slide.get("bullets") or []
    image_url = slide.get("image_url")
    bg_style = (
        f"background-image: linear-gradient(rgba(0,0,0,{'0.45' if image_url else '0'}), "
        f"rgba(0,0,0,{'0.55' if image_url else '0'})), url('{image_url}'); background-size: cover; "
        f"background-position: center;"
        if image_url
        else f"background: {colors['bg']};"
    )
    text_color = "#ffffff" if image_url else colors["title"]
    body_color = "#f3f4f6" if image_url else colors["text"]

    subtitle_html = (
        f'<p class="subtitle" style="color:{body_color}">{_escape(subtitle)}</p>' if subtitle else ""
    )
    bullets_html = "".join(
        f'<li style="animation-delay:{0.15 * (i + 1)}s">{_escape(b)}</li>' for i, b in enumerate(bullets)
    )

    return f"""
    <section class="slide" style="{bg_style}">
      <div class="slide-inner">
        <h2 style="color:{text_color}">{title}</h2>
        {subtitle_html}
        <ul style="color:{body_color}">{bullets_html}</ul>
      </div>
    </section>
    """


def build_html_preview(
    presentation_title: str,
    slides: List[dict],
    style: str = "minimal",
    language: str = "ru",
) -> str:
    colors = STYLE_CSS.get(style, STYLE_CSS["minimal"])
    slides_html = "".join(_render_slide(i, s, colors) for i, s in enumerate(slides))
    total = len(slides)
    nav_prev = "Назад" if language == "ru" else "Prev"
    nav_next = "Далее" if language == "ru" else "Next"

    return f"""<!DOCTYPE html>
<html lang="{language}">
<head>
<meta charset="UTF-8" />
<title>{_escape(presentation_title)}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ height: 100%; overflow: hidden; font-family: -apple-system, Segoe UI, Roboto, sans-serif; }}
  .deck {{ position: relative; width: 100%; height: 100vh; }}
  .slide {{
    position: absolute; inset: 0; display: none;
    align-items: center; justify-content: center;
  }}
  .slide.active {{ display: flex; }}
  .slide-inner {{ max-width: 80%; animation: fadeUp 0.5s ease both; }}
  .slide h2 {{ font-size: 2.6rem; margin-bottom: 1.2rem; font-weight: 800; animation: fadeUp 0.6s ease both; }}
  .slide .subtitle {{ font-size: 1.4rem; margin-bottom: 1.4rem; opacity: 0.85; animation: fadeUp 0.55s ease both; }}
  .slide ul {{ list-style: none; font-size: 1.3rem; line-height: 2; }}
  .slide li {{ opacity: 0; animation: fadeUp 0.5s ease forwards; }}
  @keyframes fadeUp {{
    from {{ opacity: 0; transform: translateY(16px); }}
    to {{ opacity: 1; transform: translateY(0); }}
  }}
  .nav {{
    position: fixed; bottom: 16px; left: 50%; transform: translateX(-50%);
    display: flex; gap: 10px; align-items: center;
    background: rgba(0,0,0,0.55); padding: 8px 16px; border-radius: 999px;
    font-size: 0.85rem; color: #fff; z-index: 10;
  }}
  .nav button {{
    background: {colors['accent']}; border: none; color: #fff; padding: 6px 14px;
    border-radius: 999px; cursor: pointer; font-size: 0.85rem;
  }}
  .nav button:disabled {{ opacity: 0.4; cursor: default; }}
</style>
</head>
<body>
<div class="deck" id="deck">
  {slides_html}
</div>
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
  show(0);
</script>
</body>
</html>"""
