"""Tests for the ComfyUI workflow builder, HTTP client (mocked), and fallback."""
from __future__ import annotations

from PIL import Image

from src.comfyui.backgrounds import BackgroundGenerator
from src.comfyui.client import ComfyUIClient
from src.comfyui.workflow import (build_graph, family_dims, patch_workflow,
                                  sd15_txt2img, sdxl_txt2img)
from src.core.settings import load_settings


# ---- workflow --------------------------------------------------------------
def test_sd15_workflow_structure():
    g = sd15_txt2img(prompt="a", negative="b", width=768, height=960, seed=42)
    kinds = {n["class_type"] for n in g.values()}
    assert {"CheckpointLoaderSimple", "KSampler", "CLIPTextEncode",
            "EmptyLatentImage", "VAEDecode", "SaveImage"} <= kinds
    ks = next(n for n in g.values() if n["class_type"] == "KSampler")
    assert ks["inputs"]["seed"] == 42
    assert ks["inputs"]["positive"][0] in g  # wiring points to a real node


def test_patch_workflow_updates_fields():
    g = sd15_txt2img(prompt="old", negative="neg", width=100, height=100, seed=1)
    g2 = patch_workflow(g, prompt="new prompt", seed=999, width=512, height=768)
    ks = next(n for n in g2.values() if n["class_type"] == "KSampler")
    pos_id = ks["inputs"]["positive"][0]
    assert g2[pos_id]["inputs"]["text"] == "new prompt"
    assert ks["inputs"]["seed"] == 999
    lat = next(n for n in g2.values() if n["class_type"] == "EmptyLatentImage")
    assert (lat["inputs"]["width"], lat["inputs"]["height"]) == (512, 768)
    # original graph must be untouched (deep copy)
    assert next(n for n in g.values() if n["class_type"] == "KSampler")["inputs"]["seed"] == 1


# ---- HTTP client (mocked session) -----------------------------------------
class _Resp:
    def __init__(self, status=200, data=None, content=b""):
        self.status_code = status
        self._data = data
        self.content = content
        self.text = ""

    def json(self):
        return self._data


class _FakeSession:
    def get(self, url, params=None, timeout=None):
        if "/system_stats" in url:
            return _Resp(200, {})
        if "/history/" in url:
            pid = url.rsplit("/", 1)[1]
            return _Resp(200, {pid: {
                "outputs": {"9": {"images": [
                    {"filename": "ice_bg_0001.png", "subfolder": "", "type": "output"}]}},
                "status": {"completed": True, "status_str": "success"},
            }})
        if "/view" in url:
            return _Resp(200, content=b"PNGBYTES")
        return _Resp(404)

    def post(self, url, json=None, timeout=None):
        if "/prompt" in url:
            return _Resp(200, {"prompt_id": "pid123"})
        return _Resp(404)


def test_client_generate_happy_path():
    c = ComfyUIClient("http://x", poll_interval=0.01)
    c._session = _FakeSession()
    assert c.is_ready() is True
    data = c.generate({"9": {"class_type": "SaveImage", "inputs": {}}}, timeout=2)
    assert data == b"PNGBYTES"


def test_client_not_ready_when_unreachable():
    c = ComfyUIClient("http://127.0.0.1:59999", poll_interval=0.01)
    assert c.is_ready() is False


# ---- background fallback (real image, no GPU) ------------------------------
def test_fallback_background_is_real_image(tmp_path, project_paths):
    s = load_settings(project_paths, load_dotenv=False)
    gen = BackgroundGenerator(s)
    out = tmp_path / "bg.png"
    res = gen.generate(out_path=out, background_prompt="soft gradient",
                       mood="calm", profile="neutral_soft", aspect="story",
                       try_comfyui=False)
    assert res.source == "fallback"
    assert out.exists()
    with Image.open(out) as im:
        assert im.size == family_dims(s.comfyui.model_family, "story")
        assert im.mode == "RGB"


def test_prompt_includes_profile_and_no_text_guard(project_paths):
    s = load_settings(project_paths, load_dotenv=False)
    gen = BackgroundGenerator(s)
    p = gen.build_prompt("mountains", "hopeful", "editorial_elegant")
    assert "no text" in p
    assert "editorial" in p.lower()
