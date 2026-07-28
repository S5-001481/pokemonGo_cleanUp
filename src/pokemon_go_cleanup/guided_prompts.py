# ruff: noqa: RUF001
"""Bilingual terminal prompts for the manual scan workflow."""

from __future__ import annotations

from typing import Final

import typer

from pokemon_go_cleanup.models import ScanStep
from pokemon_go_cleanup.scan import SCAN_STEPS

_GUIDED_PROMPTS: Final[dict[ScanStep, tuple[str, str, str]]] = {
    "summary": (
        "摘要 / Summary",
        "请打开目标宝可梦的详情页，并滚动到页面顶部。",
        "Open the target Pokémon detail page and scroll to the top.",
    ),
    "moves": (
        "招式 / Moves",
        "请向下滚动，直到所有招式都清晰可见。",
        "Scroll down until all moves are clearly visible.",
    ),
    "appraisal": (
        "评价 / Appraisal",
        "请打开宝可梦评价并推进对话，直到攻击、防御和 HP 个体值条全部可见。",
        (
            "Open Pokémon appraisal and advance the dialogue until the Attack, "
            "Defense, and HP IV bars are visible."
        ),
    ),
}


def prompt_guided_step(step: ScanStep) -> None:
    """Explain the manual preparation and wait for Enter."""

    title, chinese_instruction, english_instruction = _GUIDED_PROMPTS[step]
    step_number = SCAN_STEPS.index(step) + 1
    typer.echo("")
    typer.echo(f"[{step_number}/{len(SCAN_STEPS)}] {title}")
    typer.echo(chinese_instruction)
    typer.echo(english_instruction)
    typer.prompt(
        f"按 Enter 截取 {step}.png / Press Enter to capture {step}.png",
        default="",
        show_default=False,
    )
