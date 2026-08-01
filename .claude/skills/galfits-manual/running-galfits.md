# Running GalfitS

This guide explains how to run GalfitS with various command-line options, fitting methods, and troubleshooting.

## Running Methods

### Method 1: Command Line

```bash
galfits config.lyric --work ./output
```

### Method 2: MCP Interface (Programmatic)

GalfitS can also be invoked via the Model Context Protocol (MCP) interface:

```python
mcp__galmcp__run_galfits(
    config_file="config.lyric",
    timeout_sec=3600,
    extra_args=None
)
```

#### MCP Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `config_file` | str | Required | Path to `.lyric` configuration file |
| `timeout_sec` | int | 3600 | Maximum runtime in seconds |
| `extra_args` | list | None | Additional command-line arguments |

#### MCP Usage Example

```python
# Basic run
result = mcp__galmcp__run_galfits(
    config_file="/path/to/config.lyric"
)

# With timeout and custom arguments
result = mcp__galmcp__run_galfits(
    config_file="config.lyric",
    timeout_sec=7200,
    extra_args=["--fit_method", "dynesty", "--nlive", "200"]
)
```

#### MCP Return Value

Returns a dictionary containing:
- `output`: Combined stdout/stderr from GalfitS
- `exit_code`: Process exit code (0 = success)
- `artifacts`: List of generated file paths (summary, params, PNGs)

#### MCP vs Command Line

| Feature | Command Line | MCP Interface |
|---------|--------------|---------------|
| **Interactive use** | ✓ Recommended | - |
| **Scripting/automation** | Possible | ✓ Better |
| **Timeout control** | Manual | ✓ Built-in |
| **Result parsing** | Manual | ✓ Structured |
| **Artifact discovery** | Manual | ✓ Automatic |
| **Error handling** | Manual | ✓ Built-in |

## Common Command-Line Arguments

### General Options

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--config` | str | Required | Path to `.lyric` configuration file |
| `--work` / `--workplace` | str | `./` | Output directory for results |
| `--notfit` | flag | - | Skip fitting, only generate parameter file |
| `--savefull_results` | flag | - | Save full fitting results (not just summary) |
| `--saveimgs` | flag | - | Save generated model images |

### Fitting Method Selection

| Argument | Values | Default | Description |
|----------|--------|---------|-------------|
| `--fit_method` | `optimizer`, `dynesty`, `flowmc`, `chisq`, `ES` | `optimizer` | Fitting algorithm |

#### Fitting Methods Comparison

| Method | Type | Speed | Use When |
|--------|------|-------|----------|
| **optimizer** | Gradient-based | Fast | Initial fit, quick results |
| **dynesty** | Nested sampling | Medium | Bayesian inference, evidence calculation |
| **flowmc** | MCMC + flows | Slow | Complex posteriors, full MCMC chains |
| **chisq** | Chi-square minimization | Fast | Traditional fitting |
| **ES** | Evolutionary Strategy | Medium | Global optimization |

### Optimizer Options (`--fit_method optimizer`)

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--num_steps` | int | 3000 | Number of optimization steps |
| `--learning_rate` | float | 0.0008 | Learning rate |
| `--baysian` | flag | - | Enable Bayesian priors |
| `--cal_sigma` | flag | - | Calculate Hessian sigma |

**Typical usage:**
```bash
galfits config.lyric --work ./output --num_steps 5000
```

### Dynesty Options (`--fit_method dynesty`)

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--nlive` | int | 80 | Number of live points |
| `--maxiters` | int | 100000 | Maximum iterations |
| `--dlogz` | float | 0.02 | Stopping criterion (log-evidence change) |
| `--sample_method` | str | `rwalk` | Sampling method (`rwalk`, `slice`, etc.) |
| `--dynamic` | flag | - | Enable dynamic nested sampling |
| `--maxbatch` | int | 10 | Max batches for dynamic sampling |

**Typical usage:**
```bash
galfits config.lyric --work ./output --fit_method dynesty --nlive 150
```

#### Dynesty Parameters Guide

| Parameter | Effect | Higher Value | Lower Value |
|-----------|--------|--------------|-------------|
| `--nlive` | Accuracy vs speed | More accurate, slower | Faster, less accurate |
| `--dlogz` | Stopping precision | Tighter convergence | Looser convergence |
| `--maxiters` | Maximum iterations | Longer runtime | May stop early |

### FlowMC Options (`--fit_method flowmc`)

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--nchains` | int | 30 | Number of MCMC chains |
| `--rstep` | float | 0.1 | Parameter step size |
| `--nlocalsteps` | int | 3000 | Local steps per chain |
| `--nlooptraining` | int | 20 | Training loops |
| `--nloopproduction` | int | 10 | Production loops |
| `--sampler` | str | `GRW` | Sampler type (GRW, etc.) |

### Chi-Square Options (`--fit_method chisq`)

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--chimethod` | str | `scipy` | Chi-square method |
| `--scimethod` | str | `SLSQP` | SciPy method (SLSQP, L-BFGS-B) |
| `--constrain` | flag | - | Apply constraints |

### Evolutionary Strategy Options (`--fit_method ES`)

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--num_generations` | int | 10000 | Number of generations |
| `--popsize` | int | 20 | Population size |

### Parameter and Prior Options

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--readpar` | str | - | Read initial values from `.params` file |
| `--readsummary` | str | - | Read from previous summary file |
| `--priorpath` | str | - | Path to astrophysical prior file |
| `--parconstrain` | str | - | Path to `.constrain` file |
| `--weight_spec` | float | 1.0 | Weight for spectrum in joint fitting |

**Continue from previous fit:**
```bash
galfits config.lyric --work ./output --readpar previous.params
```

**Apply constraints:**
```bash
galfits config.lyric --work ./output --priorpath priors.txt --parconstrain constraints.txt
```

### AGN-Specific Options

| Argument | Description |
|----------|-------------|
| `--fixagnlinepro` | Fix AGN emission line profiles |
| `--ndisagn` | Don't display AGN in output images |

## Usage Examples by Scenario

### Quick Initial Fit (Optimizer)

**Command Line:**
```bash
galfits config.lyric --work ./output --num_steps 5000
```

**MCP Interface:**
```python
result = mcp__galmcp__run_galfits(
    config_file="config.lyric",
    extra_args=["--work", "./output", "--num_steps", "5000"]
)
```

**Use for**: First pass, getting initial parameter values

### Bayesian Analysis (Dynesty)

**Command Line:**
```bash
galfits config.lyric --work ./output --fit_method dynesty --nlive 200 --savefull_results
```

**MCP Interface:**
```python
result = mcp__galmcp__run_galfits(
    config_file="config.lyric",
    timeout_sec=7200,
    extra_args=["--fit_method", "dynesty", "--nlive", "200", "--savefull_results"]
)
```

**Use for**: Final analysis, parameter uncertainties, evidence calculation

### Refine Previous Results

**Command Line:**
```bash
galfits config.lyric --work ./output --readpar initial_fit.params --num_steps 3000
```

**MCP Interface:**
```python
result = mcp__galmcp__run_galfits(
    config_file="config.lyric",
    extra_args=["--readpar", "initial_fit.params", "--num_steps", "3000"]
)
```

**Use for**: Improving an existing fit with more steps

### Joint Imaging+Spectrum with Priors

**Command Line:**
```bash
galfits config.lyric --work ./output --priorpath priors.txt --weight_spec 0.8
```

**MCP Interface:**
```python
result = mcp__galmcp__run_galfits(
    config_file="config.lyric",
    extra_args=["--priorpath", "priors.txt", "--weight_spec", "0.8"]
)
```

**Use for**: Combined fitting when spectrum has lower quality

### Spectrum Fitting with High Resolution

**Command Line:**
```bash
# Modify config Sa4) to 1 for high-res template
galfits spectrum.lyric --work ./spec_output --fit_method optimizer --num_steps 10000
```

**MCP Interface:**
```python
# Modify config Sa4) to 1 for high-res template
result = mcp__galmcp__run_galfits(
    config_file="spectrum.lyric",
    timeout_sec=5400,
    extra_args=["--work", "./spec_output", "--fit_method", "optimizer", "--num_steps", "10000"]
)
```

**Use for**: Measuring stellar velocity dispersion

### Two-Stage Workflow

**Command Line:**
```bash
# Stage 1: Quick optimizer fit
galfits config.lyric --work ./stage1 --num_steps 3000

# Stage 2: Bayesian refinement
galfits config.lyric --work ./stage2 --readpar stage1/galaxy.params --fit_method dynesty --nlive 150
```

**MCP Interface:**
```python
# Stage 1: Quick optimizer fit
result1 = mcp__galmcp__run_galfits(
    config_file="config.lyric",
    extra_args=["--work", "./stage1", "--num_steps", "3000"]
)

# Stage 2: Bayesian refinement (using stage1 results)
result2 = mcp__galmcp__run_galfits(
    config_file="config.lyric",
    extra_args=["--work", "./stage2", "--readpar", "stage1/galaxy.params",
                "--fit_method", "dynesty", "--nlive", "150"]
)
```

### Batch Processing with MCP

```python
# Process multiple galaxies
galaxy_configs = ["galaxy1.lyric", "galaxy2.lyric", "galaxy3.lyric"]

results = []
for config in galaxy_configs:
    result = mcp__galmcp__run_galfits(
        config_file=config,
        timeout_sec=3600
    )
    results.append(result)

# Check results
for i, result in enumerate(results):
    print(f"Galaxy {i+1}: exit_code={result['exit_code']}")
    if result['exit_code'] == 0:
        print(f"  Artifacts: {result['artifacts']}")
```

## Output Files

After running, check these files in the output directory:

| File | Description |
|------|-------------|
| `*.gssummary` | Optimization summary with best-fit parameters |
| `*.params` | Machine-readable parameter table (for `--readpar`) |
| `*.sed.png` | SED plot (if SED fitting enabled) |
| `*.png` | Residual images, model images |
| `*_full_result.fits` | Full results (if `--savefull_results` used) |

### Reading the Summary File

```
# Example: galaxy.gssummary
Reduced chi^2 per band:
  FUV: 1.23
  u:   0.98
  g:   1.05
  ...

Best-fit parameters:
  bulge_Re = 1.34 ± 0.12 arcsec
  bulge_n  = 3.8 ± 0.3
  ...
```

## Performance Tips

| Tip | Description |
|-----|-------------|
| **Start with optimizer** | Fast initial fit, then switch to Dynesty |
| **Increase `--num_steps`** | For complex models or poor convergence |
| **Use `--readpar`** | To refine previous fits |
| **Adjust `--nlive`** | Higher = more accurate but slower (Dynesty) |
| **Set `--weight_spec`** | < 1.0 if spectrum has lower quality than images |
| **Check convergence** | Look at reduced χ² in summary file |

## Troubleshooting

### Fitting Convergence Issues

**Symptom**: Reduced χ² is very high or won't decrease

**Solutions**:
1. **Check initial values**: Ensure parameters are reasonable
2. **Increase steps**: `--num_steps 10000` (optimizer) or `--nlive 200` (dynesty)
3. **Expand bounds**: Verify min/max in config aren't too restrictive
4. **Check data quality**: Verify PSF, sigma images, masks

### Parameter Hits Bounds

**Symptom**: Parameter stays at min or max value

**Solutions**:
1. **Edit config**: Expand the min/max range
2. **Check .params file**: See what value it's stuck at
3. **Re-run with expanded bounds**: Use `--readpar` with new config

### Memory Issues

**Symptom**: Out of memory errors

**Solutions**:
1. **Reduce `--nlive`** for dynesty
2. **Reduce `--nchains`** for flowmc
3. **Use fewer bands** initially
4. **Reduce image size** (Ia8 parameter)

### Slow Convergence

**Symptom**: Fitting takes too long

**Solutions**:
1. **Use optimizer first**: Get good initial values
2. **Reduce `--num_steps`**: For initial exploration
3. **Simplify model**: Fewer components, fixed parameters
4. **Use lower `--nlive`**: For dynesty (trade accuracy)

### Band Misalignment

**Symptom**: Blue/red color split in residuals

**Solution**: Enable position shifts in config:
```text
Ia13) 1    # Enable shifts
Ia14) [[0,-5,5,0.1,1],[0,-5,5,0.1,1]]    # Allow fitting
```

## Workflow Recommendations

### For New Users

1. Start with example config from [examples/](examples/)
2. Use optimizer with `--num_steps 3000`
3. Check residuals and adjust config
4. Re-run with `--readpar` to refine

### For Production Analysis

1. Initial fit: `--fit_method optimizer --num_steps 5000`
2. Check .gssummary for χ² and parameters
3. Refine: `--readpar stage1.params --num_steps 5000`
4. Final: `--fit_method dynesty --nlive 200 --savefull_results`

### For Large Samples

1. Batch process with scripts
2. Use optimizer for speed
3. Save `--savefull_results` only for final sample
4. Consider using constraint files (`--priorpath`)

## Reference

- Original documentation: `/home/wnk/code/GalfitS-Public/docs/source/rungs.rst`
- GalfitS source code: https://github.com/RuancunLi/GalfitS-Public
