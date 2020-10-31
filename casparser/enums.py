import enum


class FileType(enum.IntEnum):
    """Enum for CAS file source."""

    UNKNOWN = 0
    CAMS = 1
    KFINTECH = 2
