"""Voxframe: turn audio into captioned, visually dynamic video."""

__version__ = "2.2.1"

# An installed Voxframe keeps its models in its own folder (D-156). The model
# libraries read their cache location once, when first imported, so this runs
# before anything else in the package can import them. It changes nothing in a
# source checkout, or when HF_HOME / HF_HUB_CACHE is already set.
from voxframe.config.paths import configure_model_cache as _configure_model_cache

_configure_model_cache()
