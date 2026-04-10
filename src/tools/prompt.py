"""
Prompt templates for GALFIT and GalfitS analysis using VLLM multimodal models.

This module centralizes all prompt templates used for analyzing galaxy morphology
fitting results from GALFIT (single-band) and GalfitS (multi-band) tools.
"""
import sys
from pathlib import Path
try:
    from ..prompts import prompts
except ImportError as e:
    current_file = Path(__file__).resolve()
    prompts_dir = current_file.parent.parent
    if str(prompts_dir) not in sys.path:
        sys.path.insert(0, str(prompts_dir))
    from prompts import prompts
from mcp.server.fastmcp.prompts import Prompt as MCP_Prompt

GALFIT_SYSTEM_MESSAGE = prompts.GALFIT_SYSTEM_MESSAGE
GALFITS_SYSTEM_MESSAGE = prompts.GALFITS_SYSTEM_MESSAGE
get_galfit_analysis_prompt = prompts.get_galfit_analysis_prompt
get_galfits_analysis_prompt = prompts.get_galfits_analysis_prompt
CLASSIFICATION_SYSTEM_MESSAGE = prompts.get_classification_system_message()
get_classification_prompt = prompts.get_classification_prompt
RESIDUAL_ANALYSIS_SYSTEM_MESSAGE = prompts.get_residual_analysis_system_message()
get_residual_analysis_prompt = prompts.get_residual_analysis_prompt
get_component_specification_galfit = prompts.get_component_specification_galfit
get_component_specification_galfits = prompts.get_component_specification_galfits



# ── MCP Prompt definitions (exposed for mcp_server.add_prompt) ─────

def _make_prompt(name: str, title: str, description: str, md_file: str) -> MCP_Prompt:
    """Create an MCP Prompt that returns the content of *md_file*."""
    _p = prompts  # capture singleton

    def fn() -> str:
        return _p._read_prompt(md_file)

    return MCP_Prompt(name=name, title=title, description=description, arguments=[], context_kwarg=None, fn=fn)


def _make_templated_prompt(
    name: str,
    title: str,
    description: str,
    md_file: str,
    params: dict[str, str],
) -> MCP_Prompt:
    """Create an MCP Prompt with user-supplied arguments rendered into *md_file*.

    *params* maps template variable names to their default values.
    e.g. ``{"galaxy_list": ""}`` means the prompt accepts a ``galaxy_list``
    argument with default ``""`` (optional).

    In the markdown file use ``{galaxy_list}`` (single braces) — the
    standard ``str.format()`` syntax.
    """
    _p = prompts

    # Build a function whose signature declares every key in *params*.
    # We use ``exec`` so that the parameter names become real keyword
    # arguments that FastMCP can auto-discover.
    param_defaults = tuple(params.values())
    param_names = tuple(params.keys())

    # fn body template — uses closures for _p, md_file, param_names
    def _render(*args, **kwargs):
        # Map positional args to their names
        for i, val in enumerate(args):
            if i < len(param_names):
                kwargs.setdefault(param_names[i], val)
        # Fill missing keys with their defaults
        for pn, default_val in zip(param_names, param_defaults):
            kwargs.setdefault(pn, default_val)
        return _p._read_prompt_and_render(md_file, **kwargs)

    # Dynamically create a function with the correct signature
    import inspect
    sig_params = [
        inspect.Parameter(n, inspect.Parameter.KEYWORD_ONLY, default=d)
        for n, d in zip(param_names, param_defaults)
    ]
    _render.__signature__ = inspect.Signature(sig_params)
    _render.__name__ = name
    _render.__qualname__ = name

    return MCP_Prompt.from_function(fn=_render, name=name, title=title, description=description)


workflow_galfit = _make_templated_prompt(
    name="workflow_galfit",
    title="GALFIT Workflow",
    description=(
        "Single-band galaxy morphology fitting workflow: `workflow_galfit [feedme_file]`; "
    ),
    md_file="workflow_galfit.md",
    params={"argument": ""},
)

workflow_galfits = _make_prompt(
    name="workflow_galfits",
    title="GalfitS Workflow",
    description=(
        "Multi-band galaxy morphology fitting workflow with "
        "three-phase diagnosis logic and SED modelling. `workflow_galfits [lyric_file]`"
    ),
    md_file="workflow_galfits.md",
)
