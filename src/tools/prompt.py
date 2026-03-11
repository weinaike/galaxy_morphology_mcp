"""
Prompt templates for GALFIT and GalfitS analysis using VLLM multimodal models.

This module centralizes all prompt templates used for analyzing galaxy morphology
fitting results from GALFIT (single-band) and GalfitS (multi-band) tools.
"""

from ..prompts import prompts

GALFIT_SYSTEM_MESSAGE = prompts.GALFIT_SYSTEM_MESSAGE
GALFITS_SYSTEM_MESSAGE = prompts.GALFITS_SYSTEM_MESSAGE
get_galfit_analysis_prompt = prompts.get_galfit_analysis_prompt
get_galfits_analysis_prompt = prompts.get_galfits_analysis_prompt
