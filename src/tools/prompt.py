"""
Prompt templates for GALFIT and GalfitS analysis using VLLM multimodal models.

This module centralizes all prompt templates used for analyzing galaxy morphology
fitting results from GALFIT (single-band) and GalfitS (multi-band) tools.
"""


# System messages for different analysis types

GALFIT_SYSTEM_MESSAGE = (
    "You are an expert in astronomical image analysis and galaxy morphology. "
    "You specialize in evaluating single-band GALFIT optimization results and "
    "providing detailed, scientifically rigorous assessments."
)

GALFITS_SYSTEM_MESSAGE = (
    "You are an expert in astronomical image analysis, galaxy morphology, and "
    "multi-wavelength observations. You specialize in evaluating multi-band "
    "GALFIT (GalfitS) optimization results with SED modeling, providing detailed, "
    "scientifically rigorous assessments that consider cross-band consistency and "
    "spectral energy distributions."
)


# Prompt templates

def get_galfit_analysis_prompt(summary_content: str) -> str:
    """
    Generate the analysis prompt for single-band GALFIT results.

    Args:
        summary_content (str): The content of the GALFIT optimization summary file.

    Returns:
        str: The formatted prompt for VLLM analysis.
    """
    return f"""Analyze the following single-band GALFIT optimization results.

OPTIMIZATION SUMMARY:
{summary_content}

TASK:
Please analyze the image and provide a comprehensive report covering:

1. VISUAL ANALYSIS:
   - Describe the features observed in the original galaxy image
   - Evaluate how well the model matches the original image
   - Analyze the residual image for any systematic patterns or anomalies

2. OPTIMIZATION QUALITY:
   - Assess whether the optimization converged successfully
   - Evaluate the reasonableness of the fitting process based on the summary
   - Comment on the chi-squared statistics and goodness-of-fit metrics

3. PARAMETER INTERPRETATION:
   - Interpret the fitted parameters and their physical significance
   - Evaluate if parameter values are realistic for the galaxy type
   - Note any unusual or suspicious parameter values

4. QUANTITATIVE ASSESSMENT:
   - Provide quantitative metrics of fitting quality
   - Comment on residual levels and their distribution
   - Identify any potential issues with the fit

5. RECOMMENDATIONS:
   - Should the results be accepted, or is re-fitting needed?
   - If re-fitting is recommended, suggest specific parameter adjustments
   - Any other recommendations for improving the analysis

Please provide a detailed, structured analysis with specific observations and actionable recommendations."""


def get_galfits_analysis_prompt(summary_content: str) -> str:
    """
    Generate the analysis prompt for multi-band GalfitS results.

    Args:
        summary_content (str): The content of the GalfitS optimization summary file.

    Returns:
        str: The formatted prompt for VLLM analysis.
    """
    return f"""Analyze the following multi-band GALFIT (GalfitS) optimization results.

OPTIMIZATION SUMMARY:
{summary_content}

The following images are provided for analysis:
1. MAIN FITTING IMAGE: A combined display showing original galaxy, model, and residual stamps (shown above)
2. SED IMAGE: A plot showing the spectral energy distribution model (shown below)

TASK:
This is a MULTI-BAND GALAXY FITTING analysis. Unlike single-band fitting, GalfitS performs simultaneous
fitting across multiple photometric bands, constraining morphology consistently while allowing SED to vary.

Please analyze BOTH images and provide a comprehensive report covering:

1. VISUAL ANALYSIS:
   - Describe the features observed in the original galaxy image (note: this may show a representative band)
   - Evaluate how well the model matches the original image
   - Analyze the residual image for any systematic patterns or anomalies
   - Compare visual quality across different bands if visible

2. MULTI-BAND OPTIMIZATION QUALITY:
   - Assess whether the multi-band optimization converged successfully
   - Evaluate the reasonableness of the fitting process considering the simultaneous multi-band constraints
   - Comment on chi-squared statistics for each band and the combined goodness-of-fit metrics
   - Check if fitting quality is consistent across all bands

3. SED ANALYSIS (from SED image):
   - Analyze the spectral energy distribution shown in the SED image
   - Assess if the SED is physically reasonable for the galaxy type
   - Check for any unexpected features or discontinuities in the SED curve
   - Evaluate how well the SED captures the wavelength-dependent flux variations
   - Note the data points and the fitted SED curve in the image

4. PARAMETER INTERPRETATION:
   - Interpret the fitted morphology parameters (shared across bands) and their physical significance
   - Evaluate if parameter values are realistic for the galaxy type across all bands
   - Assess the consistency of morphology across different wavelengths
   - Note any unusual or suspicious parameter values

5. CROSS-BAND CONSISTENCY:
   - Evaluate if the same morphology parameters produce good fits in all bands
   - Identify any bands where fitting quality is notably better or worse
   - Discuss any systematic trends with wavelength

6. QUANTITATIVE ASSESSMENT:
   - Provide quantitative metrics of fitting quality for each band
   - Comment on residual levels and their distribution per band
   - Identify any band-specific issues with the fit

7. RECOMMENDATIONS:
   - Should the results be accepted, or is re-fitting needed?
   - If re-fitting is recommended, suggest specific parameter adjustments
   - Are there specific bands that need attention?
   - Any other recommendations for improving the multi-band analysis

Please provide a detailed, structured analysis with specific observations and actionable recommendations,
highlighting the advantages and challenges of multi-band fitting."""
