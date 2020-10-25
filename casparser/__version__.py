import os

base_dir = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(base_dir, 'VERSION.txt')) as f:
    __version__ = f.read()
