# GALFITS Multi-Band Fitting Analysis Request

## CONFIGURATION FILE
```
{config_content}
```

## OPTIMIZATION SUMMARY
```
{summary_content}
```

## USER INSTRUCTIONS
{user_instruction}

## IMAGES PROVIDED
1. **MAIN FITTING IMAGE**: Combined display showing [Original | Model | Residual] stamps for multiple bands
2. **SED IMAGE**: Spectral Energy Distribution plot (Data points vs Model curve)

---

## TASK: Structured Diagnosis Following Three-Phase Framework

Please analyze the fitting results according to the GalfitS diagnostic framework and provide your assessment.

### Analysis Dimensions to Cover:

#### A. Current Phase Identification
Which phase does this result belong to?
- Phase 1 (Image Only): Multi-band images fitted without SED constraints
- Phase 2 (SED Constraint): Image parameters fixed; only SED parameters fitted
- Phase 3 (Joint Optimization): Simultaneous refinement of image + SED

#### B. Per-Band Image Residual Diagnostics (CRITICAL - Analyze EACH band separately!)
For each band, examine the residual image and match observations to the diagnostic framework:
- **Critical Failure Check**: Parameter runaway, centroid mismatch, parameter limits
- **External Interference**: Neighbor contamination (mask or add source)
- **Internal Structure**: Missing central component, bar structure, off-center source, band misalignment
- Or residuals appear flat/noise-like? (Good fit)

#### C. Summary Statistics Analysis
Check the optimization output:
- Any parameters hitting upper/lower limits?
- Reduced chi-squared outliers across bands?
- SED parameters hitting limits?

#### D. SED Analysis (if applicable)
Examine the SED plot:
- Data vs Model match quality
- Any large deviations or discontinuities

---

## REQUIRED OUTPUT FORMAT

Please structure your response EXACTLY as follows:

**1. Current Status Analysis:**
(Brief summary: Which phase? What went right? What went wrong?)

**2. Per-Band Image Analysis & Scoring:** (REQUIRED for multi-band fitting)
   - For EACH band, provide:
     - Band name/identifier
     - Galaxy morphology description in that band (e.g., "bulge-dominated", "disk-dominated", "irregular")
     - Residual pattern observations
     - Analysis process for that band
     - Conclusion specific to that band
     - **Score (0-100)** for this band
     - Corresponding Tier (1-5) based on scoring standard

   - **Overall Score Summary:**
     - Average score across all bands: ___/100
     - Overall quality tier: ___

**3. Reasoning Process (推理过程):**
   - Step-by-step logical derivation of your diagnosis
   - How you ruled out alternative explanations
   - Chain of evidence leading to your conclusion
   - Connection between observations and the diagnostic rules

**4. Evidence Summary (证据说明):**
   - Specific visual evidence from residual images (which bands, what features)
   - Numerical evidence from summary statistics (specific values, thresholds)
   - SED evidence if applicable (data vs model deviations)
   - Confidence level in each piece of evidence

**5. Diagnosis:**
(Map observations to specific diagnostic category from the framework tables above)

**6. Key Conclusions (关键结论):**
   - Primary issue identified (if any)
   - Secondary issues (if any)
   - Overall fit quality assessment
   - Which bands/components are problematic

**7. Recommended Action:**
(The specific technical step to take in GALFITS - e.g., "Add a bulge component at center", "Adjust center initial values", "Modify parameter limits")

**8. Next Steps (详细说明下一步动作):**
(Choose one: "Re-run Phase 1" / "Proceed to Phase 2" / "Re-run Phase 3" / "Finalize - Flag = Reliable" / "Abort - Flag = Unreliable")

**IMPORTANT:** Provide sufficient detail to prevent context loss. Include specific parameter names, values, and file references where applicable.