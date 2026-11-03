"""galfits (multi-band .lyric) format backend — stub (P0).

The dispatch seams exist (graph.py routes on ``meta["mode"]``); the
implementations land in P2 (parse_root / parse_fit / artifacts /
decode_cons_effective via beam_mb.lyric_doc + gssummary/component_attributes
parsing) and P5 (five-tuple bound-hit views). Until then every entry point
raises NotImplementedError so an accidental ``mode="galfits"`` init fails
loudly instead of silently running galfit parsing on a lyric file.
"""

from __future__ import annotations

MODE = "galfits"

_MSG = ("multi-band backend not implemented yet (P2/P5 of "
        "docs/design_mb_beam.md)")


def parse_root(root_config: str) -> dict:
    raise NotImplementedError(_MSG)


def parse_fit(run_result: dict) -> dict:
    raise NotImplementedError(_MSG)


def record_guard(fitted_inv: list[dict], input_inv: list[dict],
                 context: str = "record") -> None:
    # .lyric names live in-block (P1 rows P1) — no bare-name lesion possible
    return None


def decode_cons_effective(input_param_file: str, input_inv: list[dict]) -> dict:
    raise NotImplementedError(_MSG)


def artifacts(run_result: dict) -> dict:
    raise NotImplementedError(_MSG)


def root_artifacts(root_config: str) -> dict:
    return {"lyric": root_config}
