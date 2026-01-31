
import os
import shlex
import subprocess
from datetime import datetime
from glob import glob
from typing import Any, Annotated
import shutil
import importlib.util


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


async def run_galfits(
    config_file: Annotated[str, "the path to the GalfitS (.lyric) configuration file"],
    timeout_sec: Annotated[int, "timeout in seconds"] = 3600,
    extra_args: Annotated[list[str] | None, "extra GalfitS CLI args (e.g. ['--fit_method','optimizer','--num_steps','200'])"] = None,
) -> dict[str, Any]:
    """Execute GalfitS (multi-band) with the given config file.

    Runs GalfitS as a subprocess and returns discovered artifacts (summary + PNGs) and logs.
    """

    if not config_file or not os.path.exists(config_file):
        return {"status": "failure", "error": f"Config file not found: {config_file}"}

    config_dir = os.path.dirname(os.path.abspath(config_file))
    config_basename = os.path.splitext(os.path.basename(config_file))[0]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(os.path.join(config_dir, "output"), exist_ok=True)
    workplace_dir = os.path.join(config_dir, "output", f"{timestamp}_{config_basename}")
    os.makedirs(workplace_dir, exist_ok=True)
    shutil.copy(config_file, workplace_dir)

    cmd = _build_galfits_command(config_file=config_file, workplace=workplace_dir, saveimgs=True)
    if extra_args:
        cmd.extend([str(x) for x in extra_args])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
            cwd=config_dir,
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
    if proc.returncode != 0:
        return {
            "status": "failure",
            "error": f"GalfitS failed with return code {proc.returncode}",
            "workplace": workplace_dir,
            "command": cmd,
            "log": log,
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
    }

