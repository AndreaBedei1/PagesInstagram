"""The repair sequence is an escalation, and it has to be allowed to escalate.

``_repair_candidates()`` declares nine repairs in deliberate order — colour,
position, scrim, full overlay, font size, wrapping, and a combination — while
``quality.max_repair_attempts`` was 5. The four strongest were therefore never
reachable, and the loop gave up holding whatever the fifth had produced.

That is not a hypothesis: job 180 of the real buffer scored exactly 0.7423,
which is the best of the five reachable repairs, while the sixth (full overlay)
scored 0.9358 on the same background and would have passed. The measurements are
in reports/job_180_diagnostics.json.

The threshold is not the problem and is not touched here. A configured limit may
raise the ceiling; it may not cut the declared escalation short.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core.settings import load_settings
from src.quality.validator import REPAIR_STRATEGIES, MediaValidator
from src.rendering.renderer import DARK, LIGHT, RenderOptions, RenderResult


def _result(options: RenderOptions, *, contrast_bg=(0, 0, 0), complexity=0.30,
            font_size=80, lines=6) -> RenderResult:
    """A render whose measurements depend on the options, as a real one does."""
    colour = options.text_color or LIGHT
    return RenderResult(
        path=Path("nonexistent.png"), size=(1080, 1920), text_color=colour,
        font_size=int(font_size * options.font_scale), lines=["x"] * lines,
        scrim_strength=options.scrim_strength, box=(0, 0, 1080, 600),
        background_luminance=0.5, effective_bg_luminance=0.5,
        effective_bg_rgb=contrast_bg, text_zone_complexity=complexity,
        options=options)


class StubRenderer:
    """Records every attempt and only succeeds for the repairs asked of it.

    ``passing`` names the options that produce a good render — everything else
    comes back mediocre, exactly like a busy background that a stronger scrim
    alone cannot rescue.
    """

    def __init__(self, passes_when):
        self.passes_when = passes_when
        self.attempts: list[RenderOptions] = []

    def __call__(self, options: RenderOptions) -> RenderResult:
        self.attempts.append(options)
        colour = options.text_color or LIGHT
        if self.passes_when(options):
            # A background opposite in luminance to whatever colour the repair
            # chose: high contrast, calm zone. Returning black regardless would
            # make a repair that switches to dark text look like a failure.
            good_bg = (0, 0, 0) if colour == LIGHT else (255, 255, 255)
            return _result(options, contrast_bg=good_bg, complexity=0.05)
        # Mid grey behind the text: contrast around 5, busy zone. Scores in the
        # low 0.7s — the shape of the real failure.
        return _result(options, contrast_bg=(105, 105, 105), complexity=0.18)


@pytest.fixture
def validator(project_paths):
    return MediaValidator(load_settings(project_paths, load_dotenv=False))


def test_the_declared_sequence_is_the_documented_escalation():
    names = [s.name for s in REPAIR_STRATEGIES]
    assert names == ["text_color", "vertical_upper", "vertical_lower",
                     "scrim_medium", "scrim_strong", "full_overlay",
                     "font_scale", "box_shrink", "color_and_overlay"]


def test_a_low_configured_limit_cannot_truncate_the_escalation(validator):
    """This is the defect, stated as a rule: the floor is the declared list."""
    validator.s.quality.max_repair_attempts = 1
    assert validator.repair_limit == len(REPAIR_STRATEGIES)


def test_a_higher_configured_limit_is_honoured(validator):
    validator.s.quality.max_repair_attempts = len(REPAIR_STRATEGIES) + 4
    assert validator.repair_limit == len(REPAIR_STRATEGIES) + 4


def test_a_render_only_full_overlay_can_save_is_reached(validator):
    """The exact shape of job 180: the first five repairs are not enough."""
    validator.s.quality.max_repair_attempts = 5      # the value that broke it
    renderer = StubRenderer(lambda o: o.full_overlay)
    trace: list = []

    _result_r, validation, options = validator.render_until_valid(
        renderer, RenderOptions(), trace=trace)

    assert validation.passed, f"score {validation.score}, tentativi {trace}"
    assert options.full_overlay
    assert "full_overlay" in trace


@pytest.mark.parametrize("strategy,accepts", [
    ("full_overlay", lambda o: o.full_overlay and o.text_color is None),
    ("font_scale", lambda o: o.font_scale < 1.0),
    ("box_shrink", lambda o: o.box_shrink < 1.0),
    ("color_and_overlay", lambda o: o.full_overlay and o.text_color is not None),
])
def test_every_late_repair_is_reachable(validator, strategy, accepts):
    """Each of the four repairs the old limit hid, one at a time."""
    validator.s.quality.max_repair_attempts = 5
    renderer = StubRenderer(accepts)
    trace: list = []
    _r, validation, _o = validator.render_until_valid(renderer, RenderOptions(),
                                                      trace=trace)
    assert validation.passed, f"{strategy} non raggiunta: {trace}"
    assert strategy in trace


def test_the_loop_stops_at_the_first_repair_that_works(validator):
    """Escalating further than needed would cost renders for nothing."""
    renderer = StubRenderer(lambda o: o.text_color is not None)
    trace: list = []
    validator.render_until_valid(renderer, RenderOptions(), trace=trace)
    assert trace == ["text_color"]
    assert len(renderer.attempts) == 2          # the base render, then one repair


def test_a_render_that_passes_immediately_is_not_repaired(validator):
    renderer = StubRenderer(lambda o: True)
    trace: list = []
    _r, validation, _o = validator.render_until_valid(renderer, RenderOptions(),
                                                      trace=trace)
    assert validation.passed and trace == []
    assert len(renderer.attempts) == 1


def test_nothing_works_returns_the_best_seen_and_does_not_claim_success(validator):
    renderer = StubRenderer(lambda o: False)
    trace: list = []
    _r, validation, _o = validator.render_until_valid(renderer, RenderOptions(),
                                                      trace=trace)
    assert not validation.passed
    assert len(trace) == len(REPAIR_STRATEGIES), "l'escalation va percorsa tutta"


def test_the_quality_threshold_is_unchanged(project_paths):
    """The fix is in the loop, not in the bar it has to clear."""
    settings = load_settings(project_paths, load_dotenv=False)
    assert settings.quality.min_score == 0.75
    assert settings.quality.min_contrast_ratio == 4.5


def test_candidates_are_built_against_the_colour_actually_chosen(validator):
    """The opposite of DARK is LIGHT, and the repair has to know which was used."""
    dark_first = validator._repair_candidates(RenderOptions(), DARK)
    light_first = validator._repair_candidates(RenderOptions(), LIGHT)
    assert dark_first[0].text_color == LIGHT
    assert light_first[0].text_color == DARK
