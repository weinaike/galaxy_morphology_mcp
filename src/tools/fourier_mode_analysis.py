
import os
from typing import Annotated, Any
from .analyze_image import (
    create_vlm_client,
    encode_image_to_base64,
    call_vlm_api,
)

SYSTEM_MESSAGE = """You are a professional analyst of residual asymmetries in galaxy images. Your task is to determine whether the residual image contains lopsided, asymmetric structure that calls for a first-order azimuthal Fourier mode (m=1) correction.

Mandatory rules:
1. Every conclusion must be strictly grounded in the image content and the fitting results.
2. Describe only what is objectively visible; no subjective speculation.

Working principles:
- Observe first, judge second: objectively describe the residual features before giving any diagnostic conclusion.
- Only the first-order Fourier mode (m=1) is considered, and it may only act on the Disk component (or the single-Sersic model when no Disk component exists).
- You only assess whether the residual features support a Fourier mode; the final decision to adopt it belongs to the caller.

Positive indicators for a Fourier mode (residuals supporting m=1):
1. Dipole pattern: positive residuals on one side of the Disk region along some axis and negative residuals on the opposite side.
2. Lopsided brightness distribution: a systematic asymmetry across the Disk region that cannot be explained by mask edges, foreground stars, or companion galaxies.
3. Shells: faint concentric arc-like positive residuals in the outskirts.
4. Tidal tails: narrow, elongated bright streaks extending outward at the edges.

Negative indicators (against a Fourier mode):
1. Residuals consistent with random noise, with no systematic structure.
3. Asymmetry caused by dust lanes or foreground sources (these should be addressed by improving the mask).
4. Asymmetry of small spatial scale that does not materially affect the overall fit quality.


Initial-value guidance for the Fourier mode:
1. Amplitude
    - A dimensionless ratio $a_m$ quantifying how far the asymmetric structure deviates from the underlying perfectly symmetric model (e.g. a Sersic profile).
    - Visual residuals: inspect the residual image; if one side of the galaxy shows a bright excess of roughly 10% of the total brightness, an initial value of 0.1 is appropriate.
    - Choose the actual value from the relative brightness of the asymmetric structure in the residuals.
2. m=1 phase angle (in degrees)
    - What is the m=1 phase angle?
        - Note: it is NOT the overall major axis of the galaxy ellipse (that is m=2).
        - m=1 captures the lopsidedness / centre-of-mass skew of the galaxy.
    - Picture the galaxy as a fried egg with a bright yolk: m=1 points to the side where the "egg white" spills farthest, or where an obvious tidal tail lies. Find the direction vector from the brightest core toward the side with the most excess material.
    - Visual assessment: for m=1 (asymmetry / centre offset), note which side the galaxy "leans" toward, or where a clear tidal tail / material extension lies, and measure its position angle (0 deg = North, increasing counterclockwise).

Output format:
## 1. Objective description of residual features
(position, morphology and scale of positive and negative residuals)

## 2. Asymmetry pattern identification
(whether lopsided/asymmetric features exist and their spatial distribution; whether they can be explained by the mask / foreground sources / spiral arms / dust)

## 3. Fourier-mode decision
- recommend_fourier: yes/no
- Rationale: (based on the residual-feature analysis)
- If yes, provide suggested Fourier-mode parameters (initial values of am and theta_m, with justification)

## 4. Survey-dependent science goals
- For JWST data, the science goal is strongly focused on lopsidedness: for JWST data, recommend_fourier must be yes regardless of the image features.
- For SDSS data, judge normally.
"""




def fourier_mode_analysis(
    image_file: Annotated[str, "Path to the combined residual image file [png] from the BEST fitting round, containing three stamps: original, model, residual"],
    source_id: Annotated[str, "Identifier for the source/galaxy"] = "",
    custom_instructions: Annotated[str, "Optional context for this analysis, e.g. scientific objective or specific requirements"] = "",
) -> dict[str, Any]:
    """
    Analyze the residual image from the BEST fitting round to determine whether
    1st-order Fourier mode (m=1) should be applied to the Disk component.

    This tool is called at Step 4 of Phase 2 in the galaxy fitting workflow,
    AFTER the best fitting result has been selected and locked. It evaluates
    whether asymmetric patterns (dipole, shells, tidal tails) remain in the
    residuals that could be improved by Fourier mode correction on the Disk.

    Constraints:
    - Only 1st-order Fourier mode (m=1) is allowed
    - It can only be applied to the Disk component
    - This tool only analyzes residual features; the final decision on whether
      to actually apply Fourier mode is made by the caller using Occam's razor

    Args:
        image_file (str): Path to the combined image file from the best round,
                         containing three stamps: Original | Model | Residual.
        source_id (str): Identifier for the source/galaxy. e.g. sdss-Plate0271_MJD51883_Fiber005_r / jwst-obj28_s1_f277w 
                            Characterization data sources: SDSS, JWST, HST, S4G......
        custom_instructions (str): Optional context, e.g. scientific objective
                                  or specific analysis requirements.

    Returns:
        dict[str, Any]: A dictionary containing:
            - status (str): "success" if analysis completed, "failure" otherwise
            - analysis (str, optional): The Fourier mode analysis report (only on success)
            - analysis_file (str, optional): Path to the saved analysis file (only on success)
    """
    # Validate input file
    if not os.path.exists(image_file):
        return {"status": "failure", "error": f"Image file not found: {image_file}"}

    # Build prompt
    prompt_text = (
        f"source_id: {source_id}\n\n"
        "Based on the provided image, analyze whether the residual image contains lopsided, asymmetric structure that an m=1 Fourier mode could correct. Proceed step by step:\n\n"
        "Step 1: Objective description of the residual features\n"
        "- Describe the spatial distribution of positive and negative residuals (position, morphology, scale)\n"
        "- Pay particular attention to the Disk region and identify lopsided residual signatures:\n"
        "   - blue on one side and red on the other is the classic lopsidedness signature\n"
        "Step 2: Exclude false positives\n"
        "- An unfitted companion galaxy inside the Disk region can also produce a blue-on-one-side / red-on-the-other pattern; such cases must be excluded\n"
        "- Positive residuals from a companion inside the Disk differ clearly from lopsided positive residuals: the former is a localized, isolated source (a local centre brighter than its surroundings), while the latter is diffuse positive residual structure without a distinct local peak\n"
        "Step 3: Judge the necessity of a Fourier mode\n"
        "- Based on Steps 1 and 2, decide whether the residuals support adding an m=1 Fourier mode\n"
        "- If supported, estimate the initial amplitude from the relative brightness of the asymmetric structure in the residuals, and the initial theta_m from the direction in which the structure extends (0 deg = North, counterclockwise)\n"
        "Step 4: Formatted output\n"
    )
    if custom_instructions:
        prompt_text += f"\n\n--- Additional requirements ---\n{custom_instructions}"

    additional_content = [{"type": "text", "text": prompt_text}]

    # Create VLM client
    client, error = create_vlm_client()
    if error:
        return {"status": "failure", "error": error}

    # Encode image
    base64_image = encode_image_to_base64(image_file)
    if not base64_image:
        return {"status": "failure", "error": f"Failed to encode image: {image_file}"}

    # Call VLM
    analysis, error = call_vlm_api(
        client=client,  # type: ignore[arg-type]
        base64_image=base64_image,
        additional_content=additional_content,
        system_message=SYSTEM_MESSAGE,
    )
    if error:
        return {"status": "failure", "error": error}

    # Save analysis
    base_name = os.path.splitext(os.path.basename(image_file))[0]
    output_file = os.path.join(os.path.dirname(image_file), f"{base_name}_fourier_analysis.md")

    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(analysis)
    except Exception as e:
        print(f"Warning: Failed to save analysis to file: {e}")
        output_file = None

    return {
        "status": "success",
        "analysis": analysis,
        "analysis_file": output_file,
    }
