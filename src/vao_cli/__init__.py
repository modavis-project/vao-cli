"""Reference command-line client for Virtual Acoustic Objects."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vao-cli")
except PackageNotFoundError:  # pragma: no cover - source tree without installation
    __version__ = "0.3.0"

VAO_STANDARD_VERSION = "0.5.0"
VAO_STANDARD_VERSIONS = ("0.4.0", "0.5.0")
VAO_STANDARD_DOI = "10.5281/zenodo.22122774"
