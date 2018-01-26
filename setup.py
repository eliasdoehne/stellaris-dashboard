from distutils.core import setup
from Cython.Build import cythonize

setup(
    name="stellaristimeline",
    ext_modules=cythonize("stellaristimeline/token_value_stream.pyx"),
    requires=['Cython', 'sqlalchemy'],
    install_requires=[
        "matplotlib",
        "click",
        "numpy",
    ],

    entry_points={
        "console_scripts": [
            "stellaristimeline = stellaristimeline.main:cli",
        ],
    },
)
