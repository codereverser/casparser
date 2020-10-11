from casparser import __version__
from setuptools import setup

setup(
    name='casparser',
    version=__version__,
    packages=['casparser'],
    url='https://github.com/codereverser/casparser',
    license='MIT License',
    author='Sandeep Somasekharan',
    author_email='codereverser@gmail.com',
    description='CAS (Karvy/CAMS) PDF parser',
    install_requires=[
        'click>=7.1.2',
        'colorama>=0.4.3',
        'pdfminer.six>=20200726',
        'python-dateutil>=2.8.1',
    ],
    entry_points={
        'console_scripts': [
            'casparser = casparser.cli:cli',
        ],
    },
    classifiers=[
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
    ],
)
