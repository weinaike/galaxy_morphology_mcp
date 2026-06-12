# Parameter Files (.params and .constrain)

Parameter files in GalfitS provide direct methods for linking and constraining parameters through `.params` and `.constrain` files.

## Overview

After a GalfitS run, two files are automatically generated in the `savepath` directory:

| File | Format | Purpose |
|------|--------|---------|
| **`targetname.params`** | Machine-readable table (7 columns) | Summarizes all parameters; enables parameter linking |
| **`targetname.constrain`** | Python code | Complex constraint functions via `Update_Constraints()` |

These files can be edited and reused in subsequent runs to apply constraints.

---

## Parameter File (.params)

### File Format

The `.params` file is a machine-readable table with seven columns, accessible via `Table.read` in astropy.

| Column | Description | Editable |
|--------|-------------|----------|
| **1** | Parameter names (cannot edit) | No |
| **2** | Initial value | Yes |
| **3** | Minimum value | Yes |
| **4** | Maximum value | Yes |
| **5** | Typical variation step | Yes |
| **6** | Variable flag (1=free, 0=fixed) | Yes |
| **7** | Parameter expression (default: None) | Yes |

### Parameter Linking via Column 7

Column 7 allows direct parameter linking using expressions.

#### Syntax

```
parameter_name  min  max  step  vary  expression
```

The `expression` can reference other parameters using their names.

#### Example: Linking AGN Center to Host Galaxy

```
agn_x  0  -1  1  0.1  False  1*host_xcen
agn_y  0  -1  1  0.1  False  1*host_ycen
```

This forces the AGN position to exactly match the host galaxy center.

#### Example: Scaling Parameter

```
disk_Re  2.0  0.5  5.0  0.1  True  1.5*bulge_Re
```

The disk effective radius is constrained to be 1.5× the bulge effective radius.

#### Mathematical Operations

Supported operations in expressions:

| Operation | Example | Description |
|-----------|---------|-------------|
| Multiplication | `2*param` | Double the parameter value |
| Addition | `param + 1` | Parameter plus constant |
| Power | `param**2` | Parameter squared |
| Combination | `a*param1 + b*param2` | Linear combination |

---

## Constraint File (.constrain)

### File Format

The `.constrain` file contains a Python function `Update_Constraints()` that modifies parameters.

### Basic Structure

```python
def Update_Constraints(pardictlc):
    # pardictlc is a dictionary of all parameters
    # Modify values directly
    pardictlc['param1'] = some_expression
    pardictlc['param2'] = another_expression
```

### Variable Naming Convention

Parameter names in the constraint file follow a pattern:

```
<ComponentName><ParameterName><Suffix>
```

Examples:
- `AGNlogL5100` - AGN component, logL5100 parameter
- `HbAGNb1peak` - H-beta line, AGN component, broad component 1, peak
- `total_Re` - "total" profile, effective radius

### Example: AGN Emission Line Constraints

```python
def Update_Constraints(pardictlc):
    # Link broad H-alpha flux to L5100 (Greene & Ho 2005)
    pardictlc['HaAGNb1peak'] = 10**(1.157 * pardictlc['AGNlogL5100'] - 46.19) / (2.5 * pardictlc['HaAGNb1wid'])

    # Link broad H-beta flux to L5100
    pardictlc['HbAGNb1peak'] = 10**(1.133 * pardictlc['AGNlogL5100'] - 45.70) / (2.5 * pardictlc['HbAGNb1wid'])

    # Link narrow H-beta width to [OIII] width
    pardictlc['HbAGNn1wid'] = 0.970936 * pardictlc['OIII_5007AGNn1wid']

    # Link narrow H-beta center to [OIII] center
    pardictlc['HbAGNn1cen'] = 0.970936 * pardictlc['OIII_5007AGNn1cen']
```

### Common Use Cases

#### 1. AGN Emission Line Correlations

Link broad line luminosities to continuum luminosity:

```python
# L_Hβ ∝ L_5100^1.133 (Greene & Ho 2005)
pardictlc['HbAGNb1peak'] = 10**(1.133 * pardictlc['AGNlogL5100'] - 45.70) / (2.5 * pardictlc['HbAGNb1wid'])
```

#### 2. Narrow Line Shape Correlations

Link narrow line profiles to reduce degeneracy:

```python
# Hβ and [OIII] often have similar widths
pardictlc['HbAGNn1wid'] = 0.97 * pardictlc['OIII_5007AGNn1wid']
```

#### 3. Component Position Linking

Force components to share the same center:

```python
# AGN at galaxy center
pardictlc['AGN_x'] = pardictlc['host_xcen']
pardictlc['AGN_y'] = pardictlc['host_ycen']
```

#### 4. Structural Relations

Link structural parameters between components:

```python
# Disk position angle aligned with bulge
pardictlc['disk_PA'] = pardictlc['bulge_PA'] + 10  # Offset by 10 degrees

# Axis ratio relations
pardictlc['disk_q'] = pardictlc['bulge_q'] * 0.5  # Disk is thinner
```

---

## Command-Line Usage

### Applying Constraints

```bash
PYTHON galfitS.py --config filename.lyric --readpar paramsfile.params --parconstrain constrainfile.constrain
```

| Argument | Description |
|----------|-------------|
| `--config` | Main configuration file (.lyric) |
| `--readpar` | Path to parameter file (.params) |
| `--parconstrain` | Path to constraint file (.constrain) |

### Without Constraints

If constraint files are not specified, GalfitS will use the default parameter values from the config file.

---

## Parameter Files vs Astrophysical Priors

| Feature | Parameter Files | Astrophysical Priors |
|---------|----------------|---------------------|
| **Type** | Hard/soft linking | Probabilistic priors |
| **Format** | Table + Python code | Text file with parameters |
| **Use Case** | Exact relationships, line correlations | Physical relations with scatter |
| **Flexibility** | Very flexible (any Python code) | Predefined relations |
| **Command Flag** | `--readpar` + `--parconstrain` | `--priorpath` |

They can be used together!

---

## Tips for Writing Constraints

1. **Start Simple**: Begin with basic links, then add complexity

2. **Check Parameter Names**: Use the generated `.params` file to find exact parameter names

3. **Test Expressions**: Verify mathematical expressions produce reasonable values

4. **Use Comments**: Document constraint sources in `.constrain` file:

```python
# Greene & Ho 2005, Eq. 9 for H-beta
pardictlc['HbAGNb1peak'] = ...
```

5. **Avoid Circular Dependencies**: Don't create A→B→A loops

6. **Iterative Refinement**: Run fitting, check results, adjust constraints

---

## Common Issues

### Issue: Parameter Not Found

**Symptom**: `KeyError: 'param_name'`

**Solution**: Check the `.params` file for exact parameter name spelling. Component names matter!

### Issue: Circular Reference

**Symptom**: Infinite loop or strange behavior

**Solution**: Review constraint logic for A→B→A patterns

### Issue: Value Out of Range

**Symptom**: Parameter hits limit after constraint applied

**Solution**: Expand min/max bounds in `.params` file or adjust constraint formula

---

## See Also

- [AGN Constraints](agn-constraints.md) - AGN-specific parameter relations
- [Mass-Size Relation](mass-size-relation.md) - MSR astrophysical prior
- [Parameter Format](../model-components/parameter-format.md) - General parameter information
