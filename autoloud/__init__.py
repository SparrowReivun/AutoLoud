"""AutoLoud package and numerical runtime defaults.

Configure OpenBLAS before any submodule imports NumPy or SciPy. Their Windows
wheels can each commit hundreds of MB for the default native thread pool,
even though our small audio blocks do not benefit from parallel BLAS.
This also covers run.py, python -m autoloud and the PyInstaller entry point.
"""
import os

# Keep an explicit user override. Setting this after importing NumPy is too
# late: the native pools and their buffers have already been initialised.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
