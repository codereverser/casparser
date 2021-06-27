from importlib.metadata import version, PackageNotFoundError

FALLBACK_VERSION = "0.0.0-dev"


def get_version():
    try:
        return version("casparser")
    except PackageNotFoundError:
        return FALLBACK_VERSION  # local development version


__version__ = get_version()
