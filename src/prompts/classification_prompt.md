
## Task

observe each galaxy in the image, and then classify its morphology based on the presence of structural components.
A lower asinh_a aggressively stretches shadows to reveal faint peripheral structures, whereas a higher asinh_a preserves linear contrast to resolve intricate details within bright cores.

Please perform morphological classification following this strict three-stage reasoning process:

---

### Stage 1 — Describe Overall Structure and Composition

Provide a detailed, objective description of what you observe from images of different asinh_a values, including:
- **SNR and resolution**
- **Overall brightness distribution**
- **Isophote shape**
- **Symmetry**
- **Additional structural components**: Note any visible features such as:
  - A flattened, elongated central bar-like structure
  - Spiral arm patterns (logarithmic curves extending from center or bar ends)
  - A dominant bulge vs. extended envelope
  - Extended low-surface-brightness features (possible tidal tails, shells, or streams)

### Stage 2 — Analyze Possible Morphological Components

Based on the structural description above, analyze which physical components are likely present:

- **Bulge**: Is there a centrally concentrated, roughly spherical component? How dominant is it relative to the total light?
- **Bar**: Is there a straight, elongated structure crossing the center, wider than a typical spiral arm?
- **Spiral arms**: Are there well-defined, curving features extending from the center or bar ends?
- **Tidal features**: Are there faint, extended asymmetric structures that may indicate gravitational interaction?
- **Distinguish Disk vs Elliptical Galaxies**: Based on the overall shape and structure, determine if the galaxy is more consistent with a disk or elliptical morphology. if the evidence is ambiguous, classify as "uncertain".

### Stage 3 — Draw Conclusion

Synthesize the analysis into a final classification:

---

## Output Format

output ONLY a raw JSON object directly after the thought process. Do NOT wrap it in markdown code fences.

```json
{
  "source_id": "[XXX]",
  "galaxy_count": 0,
  "galaxies": [
    {
      "id": 1,
      "center": [x, y],
      "morphology": "disk|elliptical|uncertain",
      "bar": "present|absent|uncertain",
      "spiral_arms": "present|absent|uncertain",
      "tidal_tail": "present|absent|uncertain",
      "confidence": "high|medium|low",
      "reasoning_summary": "One-sentence summary of the key evidence driving the classification."
    }
  ]
}
```
