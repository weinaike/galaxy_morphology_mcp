
## Core Principles

1. **ALWAYS read files first** before making recommendations - never assume config contents
2. **Identify the current phase** (1/2/3) before providing diagnostics
3. **Follow the required response format** strictly
4. **Prioritize Image Analysis** when multiple issues exist
5. **Reference specific case numbers** from the diagnostic framework

---

## Three-Phase Workflow

| Phase | Description | Key Focus |
|-------|-------------|-----------|
| **Phase 1** | Image Only (no SED) | Spatial parameters: centers, Re, n, PA, q |
| **Phase 2** | SED Constraint | SED parameters only; fix spatial from Phase 1 |
| **Phase 3** | Joint Optimization | All parameters free; use Phases 1&2 as initial values |

---

# Diagnosis Logic & Rule Base (Structured Thinking)

Analyze the user's input across THREE dimensions. If multiple issues exist, prioritize "Image Analysis" FIRST.

## 1. Image Analysis (Residual Map Diagnostics)
**CRITICAL: Per-Band Analysis Required**
For multi-band fitting, you MUST analyze EACH band SEPARATELY:
- Examine residual images for each band individually
- Provide independent analysis process and conclusions for each band
- Identify which specific band(s) exhibit issues
- If issues are band-specific, clearly indicate which bands need adjustments

For each band, examine the residual image (Original - Model) for systematic patterns:

**Bad Fitting Indications & Prescribed Actions:**
*   **Case A:** Blue/Red color split in residuals (band misalignment).
    *   *Action:* Allow position shifts between bands; adjust center initial values and fitting ranges.
    *   *Band-specific:* Identify which bands are misaligned
*   **Case B:** Off-center bright source in residuals.
    *   *Action:* Add a new Sersic component at that position.
    *   *Band-specific:* Note if the off-center source appears in specific bands only
*   **Case C:** Long strip of positive residual through center.
    *   *Action:* Add a 'bar' component to the model.
    *   *Band-specific:* Check if the bar feature is visible across all bands
*   **Case D:** Circular positive residual in center.
    *   *Action:* Add a 'bulge' or 'AGN' point-source component.
    *   *Band-specific:* Determine which bands show the central excess
*   **Case E:** Model structure deviates significantly from Input image.
    *   *Action:* Re-adjust initial parameters (axis ratio, position angle, center coordinates).
    *   *Band-specific:* Identify which bands show structural deviation

**Good Fitting Indications:**
*   Residuals appear flat/noise-like (no systematic patterns).
*   Irregular residuals in high-SNR bands that do NOT match Case A-E are acceptable.

*Important Note:* IGNORE spiral arm or ring features. These are complex structures beyond current scope. Do NOT recommend adding ring components.

## 1.1 Fitting Quality Scoring Standard (百分制评分标准)
**CRITICAL:** You MUST assign a score (0-100) for EACH band individually, then calculate the overall average score.

### Five Scoring Tiers (五档评分标准):

| Tier | Score Range | Quality Level | Residual Features Description |
|------|-------------|---------------|-------------------------------|
| **Tier 1** | 80-100 | Excellent | 残差图像呈现纯噪声特征，无明显系统性结构。中心区域无明显正/负残差，边缘无扩散状残留。模型与原始图像视觉上几乎完全一致。 |
| **Tier 2** | 60-79 | Good | 残差总体呈噪声状，但存在轻微局部结构。中心或边缘有微弱系统性残差（强度<背景噪声的2倍）。整体拟合良好，仅需微调。 |
| **Tier 3** | 40-59 | Fair | 存在明显但不严重的系统性残差结构。可识别轻微的Case A-E特征，但强度中等。拟合基本可用，建议针对性优化。 |
| **Tier 4** | 20-39 | Poor | 存在强烈的系统性残差结构。Case A-E特征清晰可见，强度显著（>背景噪声3倍）。模型明显偏离数据，需重新拟合。 |
| **Tier 5** | 0-19 | Failed | 模型完全无法描述数据。残差呈现原始图像的主要结构特征，或出现严重的拟合失败迹象（如负通量、参数边界溢出）。必须重新拟合。 |

### Scoring Guidelines:
1. **Per-Band Scoring:** 每个波段独立打分，考虑该波段的SNR和残差特征
2. **Holistic Consideration:** 综合考虑残差形态、统计量（χ²）和参数合理性
3. **SNR Adjustment:** 高SNR波段要求更严格，低SNR波段可适当放宽
4. **Output Format:** 输出每个波段得分 + 总体平均分 + 对应档位

## 2. Summary Statistics Analysis
Check the optimization output for numerical issues:

*   **Check A:** Any parameter hit upper/lower limits?
    *   *Action:* Adjust the limit bounds (ensure radius < image size).
*   **Check B:** `reduced chisq` in one band >> median of others?
    *   *Action:* Flag as problematic; cross-reference with Image/SED issues.
*   **Check C:** SED parameters hit limits?
    *   *Action:* Adjust SED parameter limits.

*Conclusion:* If no issues in A/B/C, mark Summary as **Good**.


## 3. Lyric File Reference

GALFITS uses `.lyric` config files. Key parameter format:

```text
[initial_value, min, max, step, vary]
```

- `vary=1`: free parameter | `vary=0`: fixed parameter

### Phase Modifications

| Phase | Ia15 (Use SED) | Pa3-Pa8 (Spatial) | Pa9-Pa16 (SED) |
|-------|---------------|-------------------|----------------|
| 1 | 0 | vary=1 | vary=0 |
| 2 | 1 | vary=0 | vary=1 |
| 3 | 1 | vary=1 | vary=1 |

### References for configuration files:

**IMPORTANT: Use `/skill galfits-manual` to access the complete documentation before modifying configs.**

Key sections:
- **SKILL.md** - Main navigation and quick reference
- **data-config.md** - Region (R), Images (I), Spectra (S), Atlas (A)
- **model-components/** - Galaxy (G), Profile (P), Nuclei/AGN (N), Foreground Star (F)
- **examples/** - Configuration examples for different scenarios
- **running-galfits.md** - Command-line arguments and MCP interface
- **constraints/** - MSR, MMR, SFH, AGN constraints

---

## GalfitS Manual SKILL

**PURPOSE: The galfits-manual SKILL enables Claude Code to actively implement the full GalfitS workflow: config → edit → execute → analyze → iterate.**

### Closed-Loop Workflow

Claude Code uses the SKILL to perform these actions autonomously:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    GALFITS CLOSED-LOOP WORKFLOW                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  1. READ                                                             │
│     ├── Read existing .lyric config file                            │
│     ├── Read output results (.gssummary, .params, PNGs)            │
│     └── Identify current phase (1/2/3)                              │
│                                                                     │
│  2. SKILL REFERENCE (Use /skill galfits-manual)                    │
│     ├── SKILL.md - Navigation & component types                     │
│     ├── data-config.md - Ia, Sa, Aa parameters                      │
│     ├── model-components/ - P, N, G component configuration         │
│     ├── examples/ - Similar scenario templates                      │
│     └── running-galfits.md - CLI/MCP execution                     │
│                                                                     │
│  3. EDIT (Action - Modify .lyric file)                             │
│     ├── Add/modify Profile components (Pa1-Pa32)                   │
│     ├── Add/modify Nuclei/AGN components (Na1-Na27)                │
│     ├── Update Galaxy configuration (Ga1-Ga7)                      │
│     ├── Adjust parameter bounds based on analysis                   │
│     └── Configure phase-specific vary flags                         │
│                                                                     │
│  4. EXECUTE (Action - Run GalfitS)                                 │
│     ├── Use mcp__galmcp__run_galfits MCP tool                       │
│     └── Or use bash: galfits config.lyric --work ./output           │
│                                                                     │
│  5. ANALYZE (Action - Review results)                              │
│     ├── Use mcp__galmcp__galfits_analyze_by_vllm for AI diagnosis  │
│     ├── Check residual images (per-band analysis)                   │
│     ├── Review summary statistics (.gssummary)                      │
│     ├── Score fitting quality (0-100 per band)                      │
│     └── Identify issues (Case A-E, parameter limits)               │
│                                                                     │
│  6. ITERATE (Loop back to step 2 if needed)                         │
│     ├── Adjust config based on analysis                            │
│     ├── Re-run with modified parameters                            │
│     └── Continue until fit quality is acceptable                   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Action: Edit Configuration Files

When editing .lyric files, Claude Code MUST:

1. **Read current config first** - Never assume contents
2. **Reference SKILL** for correct parameter format
3. **Use Edit tool** to make specific changes

**Example: Adding a bar component**
```text
# Before: Use SKILL to understand format
/skill galfits-manual → model-components/profile-fourier.md

# Action: Edit config.lyric
Pc1) bar      # Component name
Pc2) sersic_f # Profile type (Fourier mode for bars)
Pc3) [0,-5,5,0.1,1]  # x-center
...

# Update Galaxy to include new component
Ga2) ['a','b','c']  # Add 'c' for the new bar component
```

### Action: Execute GalfitS

**Method 1: MCP Interface (Recommended)**
```python
# Claude Code directly calls MCP tool
result = mcp__galmcp__run_galfits(
    config_file="config.lyric",
    extra_args=["--fit_method", "dynesty", "--nlive", "150"]
)
```

**Method 2: Bash Command**
```bash
galfits config.lyric --work ./output --num_steps 5000
```

### Action: Analyze Results

**Method 1: MCP Analysis (Recommended)**
```python
# Claude Code directly calls MCP tool
analysis = mcp__galmcp__galfits_analyze_by_vllm(
    image_file="output/galaxy.png",      # Original|Model|Residual
    sed_file="output/galaxy.sed.png",     # SED plot
    summary_file="output/galaxy.gssummary",
    user_prompt="## CURRENT PHASE\nPhase 1\n..."
)
```

**Method 2: Manual Analysis**
- Read PNG images (visual inspection)
- Read .gssummary file (parameter statistics)
- Apply Case A-E diagnostic framework
- Score each band 0-100

### Quick Reference for Actions

| Action | Tool/SKILL | Command/Method |
|--------|------------|-----------------|
| **Read config** | Read tool | `Read: config.lyric` |
| **Get parameter format** | `/skill galfits-manual` | → SKILL.md |
| **Add Profile component** | Edit + SKILL | Edit: Add Pa/Pb/Pc section |
| **Add Nuclei component** | Edit + SKILL | Edit: Add Na section |
| **Run fit** | MCP or Bash | `mcp__galmcp__run_galfits()` |
| **Analyze results** | MCP or Read | `mcp__galmcp__galfits_analyze_by_vllm()` |
| **Get examples** | `/skill galfits-manual` | → examples/ |

### SKILL Sections for Common Edits

| Edit Task | SKILL Reference | Key Parameters |
|-----------|-----------------|----------------|
| **Add Sersic bulge** | model-components/profile-sersic.md | Pa1-Pa32, set Pa2=sersic |
| **Add Fourier bar** | model-components/profile-fourier.md | Pa1-Pa32, set Pa2=sersic_f |
| **Add AGN** | model-components/nuclei-agn.md | Na1-Na27 |
| **Fix band misalignment** | running-galfits.md → Troubleshooting | Ia13=1, Ia14 ranges |
| **Enable SED fitting** | SKILL.md → Phase-Specific | Ia15=1, Pa9-Pa16 vary=1 |
| **Apply MSR constraint** | constraints/mass-size-relation.md | --priorpath file |

### Component Type Quick Reference

| Prefix | Component | Parameters | Edit Example |
|--------|-----------|------------|--------------|
| **R** | Region | R1-R3 | `R1) MyGalaxy` |
| **I** | Image | Ia1-Ia15 | `Ia15) 1` # Use SED |
| **S** | Spectrum | Sa1-Sa4 | `Sa1) spectrum.txt` |
| **A** | Atlas | Aa1-Aa7 | `Aa2) ['a','b']` |
| **P** | Profile | Pa1-Pa32 | `Pa2) sersic` |
| **N** | Nuclei/AGN | Na1-Na27 | `Na12) ['Hb','Ha']` |
| **G** | Galaxy | Ga1-Ga7 | `Ga2) ['a','b']` |


## Available MCP Tools

### mcp__galmcp__run_galfits
Execute GalfitS multi-band fitting.

**Use when:**
- Running Phase 1, 2, or 3 fits
- Re-running after parameter adjustments

**Parameters:**
- `config_file`: Path to .lyric config file
- `timeout_sec`: Optional (default: 3600)

### mcp__galmcp__galfits_analyze_by_vllm
Analyze results using multimodal AI.

**Use when:**
- User provides residual maps and asks for diagnosis
- Evaluating whether to proceed to next phase
- Assessing fit quality

**Parameters:**
- `image_file`: Combined stamp (original|model|residual) PNG
- `sed_file`: SED plot PNG (optional)
- `summary_file`: Optimization summary file
- `user_prompt`: Structured observations

**user_prompt template:**
```text
## CURRENT PHASE
Phase 1

## USER OBSERVATIONS
### Image Residuals
- [Describe patterns]

### Summary Statistics
- [Parameters hitting limits?]
- [Reduced chi-square issues?]

### SED Analysis
- [Data vs Model quality]

## USER QUESTION
- [Specific question]
```


## Tool Integration Workflow

### Active Closed-Loop Workflow

Claude Code autonomously implements the complete workflow:

```
┌────────────────────────────────────────────────────────────────────┐
│ USER REQUEST                                                         │
│ "Analyze my GalfitS results and improve the fit"                   │
└────────────────┬───────────────────────────────────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────────────────────────────────┐
│ 1. READ & ANALYZE                                                     │
│ ├── Read config.lyric                                                │
│ ├── Read output/*.gssummary                                          │
│ ├── Read output/*.png (residual images)                              │
│ └── Identify issues (Case A-E, parameter limits)                    │
└────────────────┬───────────────────────────────────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────────────────────────────────┐
│ 2. REFERENCE SKILL                                                    │
│ /skill galfits-manual                                                │
│ ├── Find solution for identified issues                             │
│ ├── Get parameter format for new components                        │
│ └── Get examples for similar scenarios                              │
└────────────────┬───────────────────────────────────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────────────────────────────────┐
│ 3. EDIT CONFIG (Action)                                               │
│ Edit tool: config.lyric                                               │
│ ├── Add missing components (Profile, Nuclei)                         │
│ ├── Fix parameter bounds                                            │
│ ├── Enable/disable SED fitting                                      │
│ └── Adjust phase-specific vary flags                                 │
└────────────────┬───────────────────────────────────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────────────────────────────────┐
│ 4. EXECUTE (Action)                                                   │
│ mcp__galmcp__run_galfits(                                            │
│     config_file="config.lyric"                                       │
│ )                                                                    │
└────────────────┬───────────────────────────────────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────────────────────────────────┐
│ 5. ANALYZE RESULTS (Action)                                           │
│ mcp__galmcp__galfits_analyze_by_vllm(                                │
│     image_file="output/*.png"                                        │
│     summary_file="output/*.gssummary"                               │
│ )                                                                    │
│ ├── Score fitting quality (0-100 per band)                          │
│ ├── Identify remaining issues                                       │
│ └── Recommend next actions                                          │
└────────────────┬───────────────────────────────────────────────────┘
                 │
                 ▼
          ┌──────────────┐
          │ Quality OK?  │
          └──────┬───────┘
                 │
         No      │      Yes
         ┌────────┴────────┐
         ▼                 ▼
    ┌─────────┐      ┌─────────────┐
    │ ITERATE │      │ COMPLETE    │
    │         │      │ Report      │
    └────┬────┘      └─────────────┘
         │
         └──────► Back to step 2
```

### Example: Adding Bar Component

**User Request:** "There's a bar-like residual in my galaxy fit"

**Claude Code Actions:**

1. **READ**: Read config.lyric, view residual images
2. **SKILL**: `/skill galfits-manual` → profile-fourier.md
3. **EDIT**:
   ```text
   # Add to config.lyric
   Pc1) bar
   Pc2) sersic_f
   Pc3) [0,-5,5,0.1,1]
   ...
   Ga2) ['a','b','c']  # Update to include bar
   ```
4. **EXECUTE**: `mcp__galmcp__run_galfits(config_file="config.lyric")`
5. **ANALYZE**: `mcp__galmcp__galfits_analyze_by_vllm(...)`
6. **REPORT**: "Bar component added. New fit score: 75/100 (Good)"

---
