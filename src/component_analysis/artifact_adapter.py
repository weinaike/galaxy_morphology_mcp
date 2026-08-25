"""Manifest and FITS adapters for the independent component-analysis path.

The adapter consumes one explicit GalfitS round directory and lyric file. It
does not inspect or mutate the existing fitting workflow.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Mapping, MutableMapping

import numpy as np

from schemas import validate

from .derived import derive_rule_features
from .numeric import BandArrays, extract_numeric_evidence

RESULT_HDU = {
    "residual_hdu": 0,
    "mask_hdu": 1,
    "sigma_hdu": 2,
    "model_hdu": 3,
    "original_hdu": 4,
}


def _require_fits():
    try:
        from astropy.io import fits
        from astropy.wcs import WCS
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "FITS adapter requires astropy; use the project environment that "
            "declares the astropy dependency"
        ) from exc
    return fits, WCS


def _absolute_path(value: str | Path, *, field: str) -> str:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{field} does not exist: {path}")
    return str(path)


def _optional_pair_path(
    pair: Any,
    *,
    field: str,
) -> tuple[str | None, int | None]:
    is_none_pair = (
        isinstance(pair, (list, tuple))
        and bool(pair)
        and str(pair[0]).lower() == "none"
    )
    if pair is None or is_none_pair:
        return None, None
    path, hdu = _pair_path(pair, field=field)
    return path, hdu


def _pair_path(pair: Any, *, field: str) -> tuple[str, int]:
    if not isinstance(pair, (list, tuple)) or len(pair) < 2:
        raise ValueError(f"{field} must be a [path, hdu] pair")
    path = _absolute_path(pair[0], field=field)
    try:
        hdu = int(pair[1])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} HDU must be an integer") from exc
    if hdu < 0:
        raise ValueError(f"{field} HDU must be non-negative")
    return path, hdu


def _finite_fraction(data: Any) -> float | None:
    if data is None:
        return None
    array = np.asarray(data)
    return float(np.isfinite(array).mean()) if array.size else 0.0


def _parse_lyric(path: str) -> tuple[str | None, list[dict[str, Any]]]:
    """Parse only the stable lyric fields needed by this adapter.

    ``tools.parse_lyric`` also builds fitting objects and currently assumes
    science data live in HDU 0.  The artifact adapter must preserve the HDU
    numbers declared in lyric, so it keeps this small parser independent.
    """

    content = Path(path).read_text(encoding="utf-8")
    groups: dict[str, dict[int, Any]] = {}
    region: dict[int, Any] = {}
    image_pattern = re.compile(r"^I([A-Za-z])(\d+)\)\s*(.+?)\s*$")
    region_pattern = re.compile(r"^R(\d+)\)\s*(.+?)\s*$")

    def parse_pair(raw: str) -> list[Any]:
        text = raw.strip()
        try:
            value = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            values = text.strip("[]").split(",", 1)
            value = [item.strip() for item in values]
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            raise ValueError(f"invalid lyric FITS/HDU pair: {raw}")
        return [value[0], int(value[1])]

    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        image_match = image_pattern.match(line)
        if image_match:
            label, index_text, raw_value = image_match.groups()
            index = int(index_text)
            if index in {1, 3, 4, 6}:
                value = parse_pair(raw_value)
            elif index == 2:
                value = raw_value.strip()
            else:
                try:
                    value = ast.literal_eval(raw_value)
                except (SyntaxError, ValueError):
                    value = raw_value.strip()
            groups.setdefault(label, {})[index] = value
            continue
        region_match = region_pattern.match(line)
        if region_match:
            index, raw_value = region_match.groups()
            try:
                region[int(index)] = ast.literal_eval(raw_value)
            except (SyntaxError, ValueError):
                region[int(index)] = raw_value.strip()

    base = Path(path).parent
    records: list[dict[str, Any]] = []
    for label in sorted(groups):
        values = groups[label]
        required = {1, 2, 4, 6, 8}
        missing = required - values.keys()
        if missing:
            raise ValueError(f"lyric image block {label} missing fields {sorted(missing)}")
        image = list(values[1])
        psf = list(values[4])
        mask = list(values[6])
        for pair in (image, psf, mask):
            if str(pair[0]).lower() != "none":
                pair_path = Path(str(pair[0]))
                pair[0] = str(
                    pair_path.resolve()
                    if pair_path.is_absolute()
                    else (base / pair_path).resolve()
                )
        records.append(
            {
                "band": str(values[2]),
                "science": image,
                "psf": psf,
                "mask": mask,
                "fitting_area": float(values[8]),
            }
        )
    if not records:
        raise ValueError(f"no image bands found in lyric_file: {path}")
    object_name = region.get(1)
    if isinstance(object_name, (list, tuple)):
        object_name = object_name[0] if object_name else None
    return (str(object_name) if object_name else None), records


def _parse_profile_definitions(path: str) -> list[dict[str, Any]]:
    content = Path(path).read_text(encoding="utf-8")
    profiles: dict[str, dict[str, Any]] = {}
    pattern = re.compile(r"^P([a-z])(\d+)\)\s*(.+?)\s*$")
    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        match = pattern.match(line)
        if not match:
            continue
        prefix, field_text, raw_value = match.groups()
        field = int(field_text)
        if field in {1, 2}:
            value: Any = raw_value.split()[0]
        else:
            try:
                value = ast.literal_eval(raw_value)
            except (SyntaxError, ValueError):
                value = raw_value
        profiles.setdefault(prefix, {})[field] = value

    result: list[dict[str, Any]] = []
    for prefix in sorted(profiles):
        values = profiles[prefix]
        if 1 not in values or 2 not in values:
            continue
        result.append(
            {
                "name": str(values[1]),
                "type": str(values[2]),
                "n_config": values.get(6),
            }
        )
    return result


def _parse_summary_values(path: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            values[parts[0]] = float(parts[1])
        except ValueError:
            continue
    return values


def _fit_components(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    parameters = _parse_summary_values(manifest["summary_file"])
    result: list[dict[str, Any]] = []
    for profile in _parse_profile_definitions(manifest["lyric_file"]):
        name = profile["name"]
        component = {
            "name": name,
            "type": profile["type"],
            "re": parameters.get(f"{name}_Re"),
            "n": parameters.get(f"{name}_n"),
            "ba": parameters.get(f"{name}_axrat"),
            "pa": parameters.get(f"{name}_ang"),
            "n_at_boundary": False,
        }
        n_config = profile.get("n_config")
        n_value = component["n"]
        if (
            isinstance(n_config, (list, tuple))
            and len(n_config) >= 5
            and isinstance(n_value, (int, float))
            and bool(n_config[4])
        ):
            lower, upper, step = map(float, n_config[1:4])
            tolerance = max(abs(step), 1e-3)
            component["n_at_boundary"] = (
                abs(n_value - lower) <= tolerance or abs(n_value - upper) <= tolerance
            )
        result.append(component)
    return result


def _band_geometry(
    science_path: str,
    science_hdu: int,
    fitting_area: float,
) -> tuple[float, list[int]]:
    fits, WCS = _require_fits()
    with fits.open(science_path, memmap=False) as hdul:
        if science_hdu >= len(hdul) or hdul[science_hdu].data is None:
            raise ValueError(f"science HDU {science_hdu} is unavailable: {science_path}")
        data = np.asarray(hdul[science_hdu].data)
        if data.ndim != 2:
            raise ValueError(f"science HDU must be two-dimensional: {science_path}[{science_hdu}]")
        header = hdul[science_hdu].header
        wcs = WCS(header)
        try:
            from astropy.wcs.utils import proj_plane_pixel_scales

            pixscale = float(proj_plane_pixel_scales(wcs)[0] * 3600.0)
        except Exception:
            pixscale = float(abs(header.get("CDELT1", 0.0)) * 3600.0)
        if pixscale <= 0:
            raise ValueError(f"science pixel scale is unavailable: {science_path}")
        ny, nx = data.shape
    cutsize = int(fitting_area / pixscale)
    x_center, y_center = nx / 2.0, ny / 2.0
    region = [
        max(int(x_center) - cutsize, 0),
        min(int(x_center) + cutsize, nx),
        max(int(y_center) - cutsize, 0),
        min(int(y_center) + cutsize, ny),
    ]
    return pixscale, region


def _band_validation(
    *,
    result_path: str,
    science_path: str,
    science_hdu: int,
    psf_path: str | None,
    psf_hdu: int | None,
    mask_path: str,
    mask_hdu: int,
    result_hdus: Mapping[str, int],
) -> dict[str, Any]:
    fits, WCS = _require_fits()
    paths_exist = all(
        path is None or Path(path).is_file()
        for path in (result_path, science_path, psf_path, mask_path)
    )
    if not paths_exist:
        return {"paths_exist": False}

    with fits.open(result_path, memmap=False) as result_hdul:
        result_layout_valid = len(result_hdul) == 5 and all(
            index < len(result_hdul)
            and result_hdul[index].data is not None
            and np.asarray(result_hdul[index].data).ndim == 2
            for index in result_hdus.values()
        )
        result_shape = (
            np.asarray(result_hdul[result_hdus["original_hdu"]].data).shape
            if result_layout_valid
            else None
        )
        finite_values = [
            _finite_fraction(hdu.data)
            for hdu in result_hdul
            if hdu.data is not None
        ]

    with fits.open(science_path, memmap=False) as science_hdul:
        science_entry = (
            science_hdul[science_hdu] if science_hdu < len(science_hdul) else None
        )
        science_data = None if science_entry is None else science_entry.data
        science_valid = science_data is not None and np.asarray(science_data).ndim == 2
        science_shape = None if science_data is None else np.asarray(science_data).shape
        header = None if science_entry is None else science_entry.header
        try:
            wcs_valid = bool(header is not None and WCS(header).has_celestial)
        except Exception:
            wcs_valid = False
        if science_data is not None:
            finite_values.append(_finite_fraction(science_data))

    with fits.open(mask_path, memmap=False) as mask_hdul:
        mask_entry = mask_hdul[mask_hdu] if mask_hdu < len(mask_hdul) else None
        mask_data = None if mask_entry is None else mask_entry.data
        mask_valid = mask_data is not None and np.asarray(mask_data).ndim == 2
        mask_shape = None if mask_data is None else np.asarray(mask_data).shape
        if mask_data is not None:
            finite_values.append(_finite_fraction(mask_data))

    psf_valid = psf_path is None
    if psf_path is not None:
        with fits.open(psf_path, memmap=False) as psf_hdul:
            psf_entry = (
                psf_hdul[psf_hdu]
                if psf_hdu is not None and psf_hdu < len(psf_hdul)
                else None
            )
            psf_data = None if psf_entry is None else psf_entry.data
            psf_shape = None if psf_data is None else np.asarray(psf_data).shape
            psf_valid = psf_shape is not None and len(psf_shape) == 2
            if psf_data is not None:
                finite_values.append(_finite_fraction(psf_data))

    hdu_layout_valid = (
        result_layout_valid and science_valid and mask_valid and psf_valid
    )
    shape_consistent = hdu_layout_valid and result_shape == science_shape == mask_shape

    available_fractions = [value for value in finite_values if value is not None]
    return {
        "paths_exist": True,
        "hdu_layout_valid": hdu_layout_valid,
        "shape_consistent": shape_consistent,
        "wcs_valid": wcs_valid,
        "unit": None if header is None else header.get("BUNIT"),
        "finite_pixel_fraction": min(available_fractions) if available_fractions else None,
    }


def build_manifest(
    *,
    round_dir: str | Path,
    lyric_file: str | Path,
    summary_file: str | Path,
    comparison_png: str | Path | None = None,
    result_fits_by_band: Mapping[str, str | Path] | None = None,
    catalog_path: str | Path | None = None,
    round_id: str | None = None,
) -> dict[str, Any]:
    """Build and validate one explicit artifact manifest from a fitted round."""
    round_path = Path(round_dir).expanduser().resolve()
    if not round_path.is_dir():
        raise NotADirectoryError(f"round_dir does not exist: {round_path}")

    lyric_path = _absolute_path(lyric_file, field="lyric_file")
    summary_path = _absolute_path(summary_file, field="summary_file")
    comparison = (
        str(Path(comparison_png).expanduser().resolve())
        if comparison_png is not None
        else str(round_path / "all_bands_comparison.png")
    )
    if not Path(comparison).is_file():
        comparison = None

    object_name, image_infos = _parse_lyric(lyric_path)
    galaxy_id = object_name or round_path.name
    explicit_results = result_fits_by_band or {}
    bands: list[dict[str, Any]] = []

    for info in image_infos:
        band = info["band"]
        science_path, science_hdu = _pair_path(
            info["science"], field=f"{band}.science"
        )
        psf_path, psf_hdu = _optional_pair_path(info["psf"], field=f"{band}.psf")
        mask_path, mask_hdu = _pair_path(info["mask"], field=f"{band}.mask")
        result_value = explicit_results.get(
            band,
            round_path / f"{galaxy_id}_{band}_result.fits",
        )
        result_path = _absolute_path(result_value, field=f"{band}.result_fits")
        pixscale, fitting_region = _band_geometry(
            science_path,
            science_hdu,
            info["fitting_area"],
        )
        validation = _band_validation(
            result_path=result_path,
            science_path=science_path,
            science_hdu=science_hdu,
            psf_path=psf_path,
            psf_hdu=psf_hdu,
            mask_path=mask_path,
            mask_hdu=mask_hdu,
            result_hdus=RESULT_HDU,
        )
        bands.append(
            {
                "band": band,
                "science_fits": science_path,
                "science_hdu": science_hdu,
                "result_fits": result_path,
                **RESULT_HDU,
                "psf_fits": psf_path,
                "psf_hdu": psf_hdu,
                "pixscale_arcsec": pixscale,
                "fit_region": fitting_region,
                "validation": validation,
            }
        )

    catalog = {"path": None, "format": None, "available": False}
    if catalog_path is not None:
        catalog_file = _absolute_path(catalog_path, field="catalog.path")
        catalog = {
            "path": catalog_file,
            "format": Path(catalog_file).suffix.lstrip(".") or None,
            "available": True,
        }

    manifest = {
        "schema_version": "1.0",
        "round_id": round_id or round_path.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "galaxy_id": galaxy_id,
        "lyric_file": lyric_path,
        "summary_file": summary_path,
        "comparison_png": comparison,
        "bands": bands,
        "catalog": catalog,
    }
    validate(manifest, "artifact_manifest")
    return manifest


def load_band_arrays(manifest: dict[str, Any]) -> list[BandArrays]:
    """Load validated manifest FITS arrays into numeric-layer inputs."""
    validate(manifest, "artifact_manifest")
    fits, WCS = _require_fits()
    arrays: list[BandArrays] = []
    for band in manifest["bands"]:
        with fits.open(band["result_fits"], memmap=False) as result_hdul:
            original = np.asarray(result_hdul[band["original_hdu"]].data, dtype=float)
            residual = np.asarray(result_hdul[band["residual_hdu"]].data, dtype=float)
            sigma = np.asarray(result_hdul[band["sigma_hdu"]].data, dtype=float)
            mask = np.asarray(result_hdul[band["mask_hdu"]].data) > 0
        psf = None
        if band["psf_fits"] is not None:
            with fits.open(band["psf_fits"], memmap=False) as psf_hdul:
                psf = np.asarray(psf_hdul[band["psf_hdu"]].data, dtype=float)
        wcs = None
        pa_v3_deg = None
        with fits.open(band["science_fits"], memmap=False) as science_hdul:
            header = science_hdul[band["science_hdu"]].header
            try:
                candidate_wcs = WCS(header)
                if candidate_wcs.has_celestial:
                    wcs = candidate_wcs
            except (ValueError, IndexError):
                wcs = None
            try:
                candidate_pa_v3 = float(header["PA_V3"])
                if np.isfinite(candidate_pa_v3):
                    pa_v3_deg = candidate_pa_v3
            except (KeyError, TypeError, ValueError):
                pa_v3_deg = None
        if not (original.ndim == residual.ndim == sigma.ndim == 2):
            raise ValueError(f"{band['band']} result arrays must be two-dimensional")
        if residual.shape != original.shape or sigma.shape != original.shape:
            raise ValueError(f"{band['band']} result array shapes do not match")
        arrays.append(
            BandArrays(
                band=band["band"],
                original=original,
                residual=residual,
                sigma=sigma,
                psf=psf,
                mask=mask,
                source_file=band["result_fits"],
                original_hdu=band["original_hdu"],
                residual_hdu=band["residual_hdu"],
                psf_file=band["psf_fits"],
                psf_hdu=band["psf_hdu"],
                pixscale_arcsec=band["pixscale_arcsec"],
                pa_v3_deg=pa_v3_deg,
                wcs=wcs,
            )
        )
    return arrays


def extract_numeric_evidence_from_manifest(
    manifest: dict[str, Any],
    *,
    manifest_ref: str | None = None,
    isophote_cache: MutableMapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the deterministic numeric layer on a validated manifest."""
    arrays = load_band_arrays(manifest)
    primitive = extract_numeric_evidence(
        round_id=manifest["round_id"],
        manifest_ref=manifest_ref or manifest["lyric_file"],
        bands=arrays,
    )
    return derive_rule_features(
        primitive,
        arrays,
        fit_components=_fit_components(manifest),
        isophote_cache=isophote_cache,
    )
