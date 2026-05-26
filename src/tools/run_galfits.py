
import os
import re
import shlex
import subprocess
from datetime import datetime
from glob import glob
from typing import Any, Annotated
import shutil
import importlib.util


WORKFLOW_OUTPUT_DIR_RE = re.compile(r"^\d{8}_\d{6}_.+(?:_iter\d+)?$")


def _is_valid_workflow_output_dir(dirname: str) -> bool:
    """Return True only for strict timestamp-based workflow directory names."""
    return bool(WORKFLOW_OUTPUT_DIR_RE.fullmatch(dirname))


def _resolve_config_paths(config_file: str) -> str:
    """Create a temporary config with absolute paths for tools that need them.

    gsutils.read_config_file resolves relative paths against CWD, not the
    config file location. This helper rewrites ./  and ../ references to
    absolute paths so that callers don't need to care about CWD.
    """
    config_dir = os.path.dirname(os.path.abspath(config_file))
    parent_dir = os.path.dirname(config_dir)

    with open(config_file) as f:
        content = f.read()

    content = content.replace("../", parent_dir + "/")
    content = content.replace("./", config_dir + "/")

    abs_config = os.path.join(config_dir, f"_{os.path.basename(config_file)}.abs")
    with open(abs_config, "w") as f:
        f.write(content)
    return abs_config


def _build_galfits_command(config_file: str, workplace: str, saveimgs: bool) -> list[str]:
    """Build a robust command to run GalfitS.

    We avoid relying on shell aliases (common for GalfitS installs) by preferring:
    1) GALFITS_BIN (can be an executable, a .py file, or a full command string)
    2) python -m galfits.galfitS (if module is importable)
    3) fallback to `galfits` executable on PATH
    """

    galfits_bin = os.getenv("GALFITS_BIN")
    if galfits_bin:
        # Allow specifying a full command string, e.g. "python /path/to/galfitS.py"
        parts = shlex.split(galfits_bin)
        if len(parts) == 1 and parts[0].endswith(".py"):
            python_exec = os.getenv("GALFITS_PYTHON", os.getenv("PYTHON", "python3"))
            cmd = [python_exec, parts[0]]
        else:
            cmd = parts

        cmd += ["--config", config_file, "--workplace", workplace]
        if saveimgs:
            cmd.append("--saveimgs")
        return cmd

    # If GalfitS is installed as a Python package, this is the most reliable.
    # Guard against import-time failures (e.g. missing jax) during probing.
    try:
        module_ok = importlib.util.find_spec("galfits.galfitS") is not None
    except Exception:
        module_ok = False

    if module_ok:
        cmd = [os.getenv("GALFITS_PYTHON", os.getenv("PYTHON", "python3")), "-m", "galfits.galfitS"]
        cmd += ["--config", config_file, "--workplace", workplace]
        if saveimgs:
            cmd.append("--saveimgs")
        return cmd

    cmd = ["galfits", "--config", config_file, "--workplace", workplace]
    if saveimgs:
        cmd.append("--saveimgs")
    return cmd


def _parse_gssummary(summary_path: str) -> dict[str, Any]:
    """Parse a .gssummary file and extract key statistics.

    Returns a dict with reduced_chisq, bic, per_band_chisq, and parameter values.
    """
    if not summary_path or not os.path.exists(summary_path):
        return {}

    with open(summary_path) as f:
        content = f.read()

    result: dict[str, Any] = {
        "reduced_chisq": None,
        "bic": None,
        "per_band_chisq": {},
        "parameters": {},
    }

    # Global reduced chi-squared
    m = re.search(r"reduced\s+chi.*?[:\s]+([\d.]+)", content, re.IGNORECASE)
    if m:
        result["reduced_chisq"] = float(m.group(1))

    # BIC
    m = re.search(r"BIC\s*[:\s]+([\d.eE+-]+)", content, re.IGNORECASE)
    if m:
        result["bic"] = float(m.group(1))

    # Per-band chi-squared (look for patterns like "band_xxx chisq: 1.23" or in tables)
    for m in re.finditer(r"(band\s*\w+|f\d+w)\s*.*?(?:reduced\s+)?chi.*?[:\s]+([\d.]+)", content, re.IGNORECASE):
        band_name = m.group(1).strip()
        result["per_band_chisq"][band_name] = float(m.group(2))

    # Free parameters (tab-separated name-value lines)
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) == 2:
            name, value = parts
            try:
                result["parameters"][name] = float(value)
            except ValueError:
                pass

    return result


async def run_galfits(
    config_file: Annotated[str, "the path to the GalfitS (.lyric) configuration file"],
    timeout_sec: Annotated[int, "timeout in seconds"] = 3600,
    extra_args: Annotated[list[str] | None, "extra GalfitS CLI args (e.g. ['--fit_method','optimizer','--num_steps','200'])"] = None,
    read_summary: Annotated[str | None, "path to previous .gssummary to carry forward best-fit parameters"] = None,
    prior_file: Annotated[str | None, "path to .prior file for mass/size constraints"] = None,
) -> dict[str, Any]:
    """Execute GalfitS (multi-band) with the given config file.

    Runs GalfitS as a subprocess and returns discovered artifacts (summary + PNGs) and logs.
    """

    if not config_file or not os.path.exists(config_file):
        return {"status": "failure", "error": f"Config file not found: {config_file}"}

    # Validate optional inputs
    if read_summary and not os.path.exists(read_summary):
        return {"status": "failure", "error": f"Summary file not found: {read_summary}"}
    if prior_file and not os.path.exists(prior_file):
        return {"status": "failure", "error": f"Prior file not found: {prior_file}"}

    # Find galaxy root and determine workplace
    config_dir = os.path.dirname(os.path.abspath(config_file))
    config_basename = os.path.splitext(os.path.basename(config_file))[0]
    output_parent_dir = os.path.dirname(config_dir)

    if os.path.basename(output_parent_dir) == "output":
        galaxy_dir = os.path.dirname(output_parent_dir)
        subdir = os.path.basename(config_dir)
        if _is_valid_workflow_output_dir(subdir):
            workplace_dir = config_dir
            os.makedirs(workplace_dir, exist_ok=True)
            work_cwd = galaxy_dir
        else:
            return {
                "status": "failure",
                "error": (
                    f"Invalid workflow output directory name: {subdir}. "
                    "Expected YYYYMMDD_HHMMSS_<basename> or "
                    "YYYYMMDD_HHMMSS_<basename>_iterN. "
                    "Legacy roundN naming is not supported."
                ),
            }
    else:
        galaxy_dir = config_dir
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        workplace_dir = os.path.join(galaxy_dir, "output", f"{timestamp}_{config_basename}")
        os.makedirs(workplace_dir, exist_ok=True)
        shutil.copy(config_file, workplace_dir)
        work_cwd = galaxy_dir

    cmd = _build_galfits_command(config_file=config_file, workplace=workplace_dir, saveimgs=True)

    # Add --readsummary to carry forward previous round parameters
    if read_summary:
        cmd.extend(["--readsummary", os.path.abspath(read_summary)])

    # Add --prior for mass/size constraints
    if prior_file:
        cmd.extend(["--prior", os.path.abspath(prior_file)])

    if extra_args:
        cmd.extend([str(x) for x in extra_args])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
            cwd=work_cwd,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "failure",
            "error": f"GalfitS execution timed out after {timeout_sec} seconds",
        }
    except FileNotFoundError:
        return {
            "status": "failure",
            "error": "GalfitS executable not found. Set GALFITS_BIN (or install GalfitS as a Python package).",
            "command": cmd,
        }

    log = (proc.stdout or "") + (proc.stderr or "")

    # Save log to workplace (both success and failure)
    log_path = os.path.join(workplace_dir, "run.log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(log)

    if proc.returncode != 0:
        return {
            "status": "failure",
            "error": f"GalfitS failed with return code {proc.returncode}",
            "workplace": workplace_dir,
            "command": cmd,
            "log": log,
            "log_path": log_path,
        }

    # Discover common outputs
    summary_files = sorted(glob(os.path.join(workplace_dir, "*.gssummary")))

    # GalfitS output filenames vary between versions; support common patterns.
    imagefit_pngs = sorted(
        set(
            glob(os.path.join(workplace_dir, "*.imagefit.png"))
            + glob(os.path.join(workplace_dir, "*image_fit.png"))
            + glob(os.path.join(workplace_dir, "*imagefit*.png"))
        )
    )
    sedmodel_pngs = sorted(
        set(
            glob(os.path.join(workplace_dir, "*.sedmodel.png"))
            + glob(os.path.join(workplace_dir, "*SED_model.png"))
            + glob(os.path.join(workplace_dir, "*sed*model*.png"))
        )
    )

    result_fits = sorted(set(glob(os.path.join(workplace_dir, "*_result.fits"))))

    # Additional output files from GalfitS
    constrain_files = sorted(glob(os.path.join(workplace_dir, "*.constrain")))
    params_files = sorted(glob(os.path.join(workplace_dir, "*.params")))

    # Parse gssummary for structured statistics
    summary_stats = _parse_gssummary(summary_files[0]) if summary_files else {}

    return {
        "status": "success",
        "message": f"GalfitS completed successfully for {config_file}. Output files:\n"
        f"- summary_files : .gssummary files contain fitting parameters, χ² statistics, and model components for all bands\n"
        f"- imagefit_pngs : PNG visualizations showing observed data, model fits, and residuals for all image bands\n"
        f"- sedmodel_pngs : PNG plots of Spectral Energy Distribution (SED) models showing multi-band flux fitting across wavelengths\n"
        f"- result_fits : FITS files containing the best-fit model results\n"
        f"- constrain_files : Constraint files used during fitting\n"
        f"- params_files : Parameter files with initial and fitted values",
        "workplace": workplace_dir,
        "summary_files": summary_files,
        "imagefit_pngs": imagefit_pngs,
        "sedmodel_pngs": sedmodel_pngs,
        "result_fits": result_fits,
        "constrain_files": constrain_files,
        "params_files": params_files,
        "log_path": log_path,
        "reduced_chisq": summary_stats.get("reduced_chisq"),
        "bic": summary_stats.get("bic"),
        "per_band_chisq": summary_stats.get("per_band_chisq", {}),
        "parameters": summary_stats.get("parameters", {}),
    }

async def run_galfits_image_fitting(
    config_file: Annotated[str, "the path to the GalfitS (.lyric) configuration file"],
    timeout_sec: Annotated[int, "timeout in seconds"] = 3600,
    extra_args: Annotated[list[str] | None, "extra GalfitS CLI args (e.g. ['--fit_method','optimizer','--num_steps','200'])"] = None,
) -> dict[str, Any]:
    """Execute GalfitS (multi-band) with the given config file for image fitting.

    It runs GalfitS as a subprocess and returns discovered artifacts (summary + PNGs) and logs.
    """
    return await run_galfits(config_file=config_file, timeout_sec=timeout_sec, extra_args=extra_args)

async def run_galfits_sed_fitting(
    config_file: Annotated[str, "the path to the GalfitS (.lyric) configuration file"],
    image_fitting_workplace: Annotated[str, "the workplace directory containing results from image fitting, required for sed fitting"],
    timeout_sec: Annotated[int, "timeout in seconds"] = 3600,
    extra_args: Annotated[list[str] | None, "extra GalfitS CLI args (e.g. ['--fit_method','optimizer','--num_steps','200'])"] = None,
) -> dict[str, Any]:
    """
    Execute GalfitS (multi-band) with the given config file for sed fitting.
    It runs GalfitS as a subprocess and returns a new lyric file that will be used for combined image and sed fitting.
    The image_fitting_workplace is used to provide the necessary image fitting results for the sed fitting step.
    """
    from .galfits_fitting import PureSEDFitting
    #from src.tools.galfits_fitting import PureSEDFitting

    config_dir = os.path.dirname(os.path.abspath(config_file))
    config_basename, config_ext = os.path.splitext(os.path.basename(config_file))
    # Find galaxy root dir: if config is already inside output/, go up two levels
    output_parent = os.path.dirname(config_dir)
    if os.path.basename(output_parent) == "output" and _is_valid_workflow_output_dir(os.path.basename(config_dir)):
        galaxy_dir = os.path.dirname(output_parent)
    else:
        galaxy_dir = config_dir
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_base = os.path.join(galaxy_dir, "output")
    os.makedirs(output_base, exist_ok=True)
    workplace_dir = os.path.join(output_base, f"{timestamp}_{config_basename}_sed")
    os.makedirs(workplace_dir, exist_ok=True)
    shutil.copy(config_file, workplace_dir)
    new_lyric_file = os.path.join(workplace_dir, f"{config_basename}_for_image_sed_fitting" + (f"{config_ext}" if config_ext else ""))

    # gsutils.read_config_file resolves relative paths against CWD, not the
    # config file location. Create an absolute-path copy to avoid FileNotFoundError.
    abs_config = _resolve_config_paths(config_file)

    res = PureSEDFitting(lyric_file=abs_config, workplace=image_fitting_workplace, new_lyric_file=new_lyric_file, mock_root=workplace_dir, args=extra_args)
    if res.get("status") != "success":
        return {
            "status": "failure",
            "message": f"SED fitting in the workplace {workplace_dir} failed: {res.get('message', 'Unknown error')}"
        }
    return {
        "status": "success",
        "message": f"SED fitting completed successfully. New lyric file for image-sed fitting generated: {new_lyric_file}"
    }    

async def run_galfits_image_sed_fitting(
    config_file: Annotated[str, "the path to the GalfitS (.lyric) configuration file"],
    timeout_sec: Annotated[int, "timeout in seconds"] = 3600,
    extra_args: Annotated[list[str] | None, "extra GalfitS CLI args (e.g. ['--fit_method','optimizer','--num_steps','200'])"] = None,
) -> dict[str, Any]:
    """Execute GalfitS (multi-band) with the given config file for combined image and sed fitting.

    It runs GalfitS as a subprocess and returns discovered artifacts (summary + PNGs) and logs.
    """
    return await run_galfits(config_file=config_file, timeout_sec=timeout_sec, extra_args=extra_args)

if __name__ == '__main__':
    config_file = "/home/jiangbo/galaxy_morphology_mcp/GALFITS_examples/latest/configs/obj692"    
    image_fitting_workplace = "/home/jiangbo/GALFITS_examples/latest/results/obj692"
    import asyncio
    res = asyncio.run(run_galfits_sed_fitting(config_file=config_file, image_fitting_workplace=image_fitting_workplace, extra_args=["--fit_method", "ES"]))
    print(res)
