Please inspect the three images of this galaxy (Image 1: Global View of the entire disk, Image 2: Center Optical Zoom with 5-sigma green contour, Image 3: First-Round Model Residual Zoom).

Extract the 11 structural features precisely according to your operational instructions.

Output exactly one raw JSON object on the last line matching the schema:
{
  "is_spiral": true | false,
  "inclination_deg": <float 0-90>,
  "disk_pa_deg": <float 0-180>,
  "has_outer_disk": "disk" | "spheroid" | "uncertain",
  "optical_core_pa_deg": <float 0-180>,
  "optical_core_axis_ratio": <float >= 1.0>,
  "spiral_arm_takeoff": "elbow" | "curved_shoulder" | "smooth" | "none",
  "residual_has_discrete_ansae": true | false,
  "residual_has_solid_pillar": true | false,
  "residual_ring_type": "none" | "concentric_360" | "outer_resonance_ring_with_inner_bar",
  "residual_core_contrast": "flat_clean" | "bullseye_concentric" | "strong_bipolar_bar" | "diffuse_gradient",
  "has_unmasked_companion": true | false,
  "morphology_notes": "<concise description of key visual findings>"
}
