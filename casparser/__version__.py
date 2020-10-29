from importlib.metadata import version
import os

base_dir = os.path.dirname(os.path.abspath(__file__))
version_filename = os.path.join(base_dir, "VERSION.txt")
if os.path.exists(version_filename):
    with open(version_filename) as f:
        __version__ = f.read()  # local dev
else:
    __version__ = version("casparser")  # installed
