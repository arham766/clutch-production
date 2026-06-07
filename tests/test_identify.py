"""Tests for perception.identify + frame selection (mock vision provider, no keys)."""

from __future__ import annotations

import io
import os

from PIL import Image

from contracts import CatalogEntry
from perception.frames import FrameGate, select_best_frames
from perception.identify import identify
from perception.providers.mock import MockVisionProvider

CATALOG = [
    CatalogEntry(product_id="lj-m404", name="LaserJet Pro M404", brand="HP"),
    CatalogEntry(product_id="oj-9015", name="OfficeJet Pro 9015", brand="HP"),
]


def _png(color=(128, 128, 128), size=(64, 64), noise=False) -> bytes:
    if noise:
        img = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    else:
        img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ----------------------------------------------------------------- identify
async def test_confident_match_resolves():
    prov = MockVisionProvider({
        "product_id": "lj-m404", "confidence": 0.92,
        "candidates": [{"product_id": "lj-m404", "confidence": 0.92}],
        "model_source": "ocr_label", "raw_text": "HP LaserJet Pro M404dn",
        "brand": "HP", "model": "M404",
        "problem": {"indicators": ["amber light"], "summary": "paper jam"},
    })
    r = await identify([_png(noise=True)], CATALOG, provider=prov)
    assert r.product_id == "lj-m404"
    assert r.resolved and not r.needs_confirmation
    assert "amber light" in r.problem.indicators
    assert "M404" in r.raw_text
    assert r.problem.to_query()


async def test_low_confidence_needs_confirmation():
    prov = MockVisionProvider({
        "product_id": "lj-m404", "confidence": 0.4,
        "candidates": [{"product_id": "lj-m404", "confidence": 0.4}], "problem": {},
    })
    r = await identify([_png(noise=True)], CATALOG, provider=prov)
    assert r.needs_confirmation and not r.resolved


async def test_unknown_product_rejected():
    prov = MockVisionProvider({"product_id": "not-in-catalog", "confidence": 0.95, "problem": {}})
    r = await identify([_png(noise=True)], CATALOG, provider=prov)
    assert r.product_id is None and r.needs_confirmation


async def test_ambiguous_candidates_need_confirmation():
    prov = MockVisionProvider({
        "product_id": "lj-m404", "confidence": 0.6,
        "candidates": [
            {"product_id": "lj-m404", "confidence": 0.6},
            {"product_id": "oj-9015", "confidence": 0.55},   # within 0.15 margin
        ], "problem": {},
    })
    r = await identify([_png(noise=True)], CATALOG, provider=prov)
    assert r.needs_confirmation
    assert [c.product_id for c in r.candidates] == ["lj-m404", "oj-9015"]


async def test_no_frames_asks_user_and_skips_provider():
    prov = MockVisionProvider({"product_id": "lj-m404", "confidence": 0.99, "problem": {}})
    r = await identify([], CATALOG, provider=prov)
    assert r.product_id is None and r.needs_confirmation
    assert prov.calls == []          # provider not called when there's no usable frame


async def test_default_mock_matches_first_catalog_entry():
    prov = MockVisionProvider()       # default responder
    r = await identify([_png(noise=True)], CATALOG, provider=prov)
    assert r.product_id == "lj-m404" and r.resolved


# ----------------------------------------------------------------- frames
def test_select_best_frames_picks_sharpest_and_dedups():
    sharp = _png(noise=True)                 # high edge variance
    blurry = _png(color=(120, 120, 120))     # flat → ~0 sharpness
    near_dup = _png(color=(121, 121, 121))   # ~identical ahash to blurry
    out = select_best_frames([blurry, sharp, near_dup], k=2)
    assert out[0] == sharp                   # sharpest first
    assert len(out) == 2                     # blurry + near_dup collapse to one
    assert sharp in out


def test_select_best_frames_empty():
    assert select_best_frames([], k=2) == []


# ----------------------------------------------------------------- FrameGate (continuous feed)
def test_frame_gate_skips_unchanged_view():
    g = FrameGate(change_distance=10, min_interval_s=0.0)   # no rate cap; test change only
    a = _png(noise=True)
    b = _png(noise=True)                                    # different scene
    assert g.should_process(a, now=0.0) is True            # first frame → look
    assert g.should_process(a, now=1.0) is False           # identical view → skip the VLM
    assert g.should_process(b, now=2.0) is True            # meaningful change → look again


def test_frame_gate_rate_cap():
    g = FrameGate(change_distance=0, min_interval_s=1.0)    # always "changed"; test rate cap
    a, b = _png(noise=True), _png(noise=True)
    assert g.should_process(a, now=0.0) is True
    assert g.should_process(b, now=0.4) is False           # within min_interval → capped
    assert g.should_process(b, now=1.5) is True            # past the cap → look


def test_frame_gate_unreadable_frame():
    g = FrameGate(min_interval_s=0.0)
    assert g.should_process(b"not an image", now=0.0) is False


# ----------------------------------------------------------------- real path (no keys, no openai)
import json as _json
from types import SimpleNamespace

from perception import canned_identify, make_identify


class _FakeGateway:
    """Minimal OpenAI-compatible stand-in: records the call, returns canned content."""

    def __init__(self, content):
        self._content = content              # dict (→ JSON) or raw str
        self.last_call = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, *, model, messages, **kw):
        self.last_call = {"model": model, "messages": messages, **kw}
        text = self._content if isinstance(self._content, str) else _json.dumps(self._content)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


async def test_gateway_provider_uses_logical_model_and_sends_ref_photo():
    gw = _FakeGateway({
        "product_id": "lj-m404", "confidence": 0.9,
        "candidates": [{"product_id": "lj-m404", "confidence": 0.9}],
        "model_source": "visual_match", "raw_text": "", "brand": "HP", "model": "M404",
        "problem": {"indicators": ["amber light"], "summary": "jam"},
    })
    catalog = [CatalogEntry(product_id="lj-m404", name="LaserJet Pro M404", brand="HP",
                            ref_image=_png(noise=True))]
    identify_fn = make_identify(gw, model="vision.identify")   # LOGICAL gateway name, not a vendor
    r = await identify_fn([_png(noise=True)], catalog)
    assert r.product_id == "lj-m404" and r.resolved
    assert gw.last_call["model"] == "vision.identify"          # swap-safe: routed by logical name
    content = gw.last_call["messages"][0]["content"]
    img_parts = [p for p in content if p.get("type") == "image_url"]
    assert len(img_parts) >= 2                                  # customer frame + reference photo


async def test_gateway_provider_parses_fenced_json():
    gw = _FakeGateway('```json\n{"product_id":"lj-m404","confidence":0.9,'
                      '"candidates":[{"product_id":"lj-m404","confidence":0.9}],"problem":{}}\n```')
    identify_fn = make_identify(gw, model="vision.identify")
    r = await identify_fn([_png(noise=True)],
                          [CatalogEntry(product_id="lj-m404", name="M404", brand="HP")])
    assert r.product_id == "lj-m404" and r.resolved


async def test_visual_match_path_no_label():
    prov = MockVisionProvider({
        "product_id": "oj-9015", "confidence": 0.88,
        "candidates": [{"product_id": "oj-9015", "confidence": 0.88}],
        "model_source": "visual_match", "raw_text": "", "problem": {},
    })
    r = await identify([_png(noise=True)], CATALOG, provider=prov)
    assert r.product_id == "oj-9015" and r.model_source == "visual_match" and r.raw_text == "" and r.resolved


# ---------------------------------------------------------- model_source inference (never "none" on a match)
async def test_source_inferred_ocr_when_model_omits_it_but_label_read():
    prov = MockVisionProvider({                       # model didn't report a source, but read a label
        "product_id": "lj-m404", "confidence": 0.95, "raw_text": "HP LaserJet Pro M404",
        "candidates": [{"product_id": "lj-m404", "confidence": 0.95}], "problem": {},
    })
    r = await identify([_png(noise=True)], CATALOG, provider=prov)
    assert r.resolved and r.model_source == "ocr_label"


async def test_source_inferred_visual_when_model_omits_it_and_no_label():
    prov = MockVisionProvider({                       # no source, no OCR text → matched by appearance
        "product_id": "lj-m404", "confidence": 0.95, "raw_text": "",
        "candidates": [{"product_id": "lj-m404", "confidence": 0.95}], "problem": {},
    })
    r = await identify([_png(noise=True)], CATALOG, provider=prov)
    assert r.resolved and r.model_source == "visual_match"


async def test_source_never_none_on_confident_match():
    prov = MockVisionProvider({                       # the live-stream contradiction: matched yet "none"
        "product_id": "lj-m404", "confidence": 1.0, "model_source": "none", "raw_text": "",
        "candidates": [{"product_id": "lj-m404", "confidence": 1.0}], "problem": {},
    })
    r = await identify([_png(noise=True)], CATALOG, provider=prov)
    assert r.resolved and r.model_source == "visual_match"   # bogus self-report overridden


async def test_source_none_when_no_catalog_match():
    prov = MockVisionProvider({"product_id": "not-in-catalog", "confidence": 0.95,
                               "model_source": "visual_match", "problem": {}})
    r = await identify([_png(noise=True)], CATALOG, provider=prov)
    assert r.product_id is None and r.model_source == "none"   # no match → "none" regardless of claim


async def test_ocr_evidence_overrides_model_claimed_visual():
    prov = MockVisionProvider({                       # model said visual, but it clearly read a label
        "product_id": "lj-m404", "confidence": 0.95, "model_source": "visual_match",
        "raw_text": "HP LaserJet Pro M404",
        "candidates": [{"product_id": "lj-m404", "confidence": 0.95}], "problem": {},
    })
    r = await identify([_png(noise=True)], CATALOG, provider=prov)
    assert r.model_source == "ocr_label"


def test_canned_identify_resolves():
    r = canned_identify([_png()], CATALOG)
    assert r.product_id == "lj-m404" and r.resolved
    assert r.problem.indicators
