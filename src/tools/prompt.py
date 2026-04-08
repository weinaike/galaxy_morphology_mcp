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

GALFIT_SYSTEM_MESSAGE = prompts.GALFIT_SYSTEM_MESSAGE
GALFITS_SYSTEM_MESSAGE = prompts.GALFITS_SYSTEM_MESSAGE
get_galfit_analysis_prompt = prompts.get_galfit_analysis_prompt
get_galfits_analysis_prompt = prompts.get_galfits_analysis_prompt
CLASSIFICATION_SYSTEM_MESSAGE = prompts.get_classification_system_message()
get_classification_prompt = prompts.get_classification_prompt
RESIDUAL_ANALYSIS_SYSTEM_MESSAGE = prompts.get_residual_analysis_system_message()
get_residual_analysis_prompt = prompts.get_residual_analysis_prompt
