"""Tests for typography layout and rendering (synthetic backgrounds, no GPU)."""
from __future__ import annotations

from PIL import Image, ImageDraw

from src.core.settings import load_settings
from src.rendering import Renderer
from src.rendering.fonts import FontResolver
from src.rendering.layout import fit_text, wrap_text
from src.rendering.renderer import DARK, LIGHT


def _bg(tmp_path, color, name="bg.png", size=(900, 1200)):
    p = tmp_path / name
    Image.new("RGB", size, color).save(p)
    return p


def test_wrap_and_fit(project_paths):
    s = load_settings(project_paths, load_dotenv=False)
    resolver = FontResolver(s.paths.fonts)
    img = Image.new("RGB", (1080, 1350))
    draw = ImageDraw.Draw(img)
    text = "La costanza quotidiana trasforma piccoli gesti in grandi risultati nel tempo."
    fr = fit_text(draw, text, resolver, "sans_semibold", box_w=800, box_h=600,
                  max_font=120, min_font=28, max_lines=6)
    assert fr.lines
    assert len(fr.lines) <= 6
    assert fr.text_width <= 801
    assert fr.text_height <= 600
    # every wrapped line must fit the width
    for ln in fr.lines:
        assert draw.textlength(ln, font=fr.font) <= 801


def test_render_size_and_dark_text_on_light_bg(tmp_path, project_paths):
    s = load_settings(project_paths, load_dotenv=False)
    r = Renderer(s)
    bg = _bg(tmp_path, (240, 240, 238))
    res = r.render(background_path=bg, out_path=tmp_path / "out.png",
                   text="Un passo alla volta costruisci il tuo domani.",
                   content_type="motivational", aspect="feed")
    assert res.size == tuple(s.rendering.post_size)
    assert res.text_color == DARK
    with Image.open(res.path) as im:
        assert im.size == tuple(s.rendering.post_size)


def test_render_light_text_on_dark_bg(tmp_path, project_paths):
    s = load_settings(project_paths, load_dotenv=False)
    r = Renderer(s)
    bg = _bg(tmp_path, (18, 18, 22))
    res = r.render(background_path=bg, out_path=tmp_path / "dark.png",
                   text="Il coraggio nasce dove finisce la paura.",
                   content_type="motivational", aspect="story")
    assert res.text_color == LIGHT
    assert res.size == tuple(s.rendering.story_size)


def test_quote_shows_author(tmp_path, project_paths):
    s = load_settings(project_paths, load_dotenv=False)
    r = Renderer(s)
    bg = _bg(tmp_path, (230, 228, 224))
    res = r.render(background_path=bg, out_path=tmp_path / "q.png",
                   text="Ogni cosa a suo tempo.", content_type="famous_quote",
                   aspect="feed", author="Seneca", show_author=True)
    assert res.metadata["has_author"] is True


def test_long_text_still_fits_max_lines(tmp_path, project_paths):
    s = load_settings(project_paths, load_dotenv=False)
    r = Renderer(s)
    bg = _bg(tmp_path, (235, 235, 235))
    long = ("La disciplina costante e paziente trasforma nel tempo ogni piccola "
            "azione quotidiana in un risultato solido, concreto e duraturo per te.")
    res = r.render(background_path=bg, out_path=tmp_path / "long.png",
                   text=long, content_type="motivational", aspect="feed")
    assert len(res.lines) <= s.rendering.max_lines
    assert res.font_size >= 28
