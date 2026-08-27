class VAOCLIError(Exception):
    """Expected user-facing failure."""


class NetworkError(VAOCLIError):
    """A trusted remote service could not be read safely."""


class IntegrityError(VAOCLIError):
    """Exact record, archive, or content integrity did not hold."""


class ResolutionError(VAOCLIError):
    """A DOI or VAO file could not be resolved unambiguously."""


class ConfigurationError(VAOCLIError):
    """A required local dependency or configuration is unavailable."""


class UnsupportedError(VAOCLIError):
    """The requested operation is intentionally unsupported."""
