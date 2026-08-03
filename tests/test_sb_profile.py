"""Regression tests for graceful 1D surface-brightness-profile fallback."""

import matplotlib.pyplot as plt
import numpy as np

from tools import sb_profile


def test_render_sb_profile_returns_pair_when_isophote_fit_fails(monkeypatch):
    """An empty isophote result must remain safe for two-value unpacking."""
    monkeypatch.setattr(sb_profile, "fit_data_isophotes", lambda *args, **kwargs: (None, None))
    fig, (ax_main, ax_resid) = plt.subplots(2, 1)
    image = np.ones((32, 32), dtype=float)

    try:
        result = sb_profile.render_sb_profile(
            ax_main,
            ax_resid,
            original_data=image,
            sigma_data=None,
            model_data=image,
            param_file=None,
            components=None,
            fit_region=None,
            auto_sky=True,
        )
    finally:
        plt.close(fig)

    assert result == (None, None)


def test_fit_data_isophotes_without_photutils_preserves_return_shape(monkeypatch):
    """auto_sky mode always returns ``(isolist, sky_value)``."""
    monkeypatch.setattr(sb_profile, "HAS_PHOTUTILS", False)
    image = np.ones((16, 16), dtype=float)

    assert sb_profile.fit_data_isophotes(image, auto_sky=True) == (None, None)
    assert sb_profile.fit_data_isophotes(image, auto_sky=False) is None
