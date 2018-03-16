import platform

from setuptools import setup, find_packages

try:
    from Cython.Build import cythonize

    extension_modules = cythonize("src/stellarisdashboard/cython_ext/token_value_stream.pyx")
    # Currently, the cython extensions do not work on windows. For now, disable them
    if platform.platform() == "Windows":
        extension_modules = []

except ImportError:
    print("Cython is not installed, using slow parser.")
    extension_modules = []
except RuntimeError as e:
    print(f"Warning: RuntimeError while preparing Cython extension: {e}")
    print("Falling back to slow parser.")
    extension_modules = []

setup(
    name="stellarisdashboard",
    ext_modules=extension_modules,
    packages=find_packages("src"),
    package_dir={"": "src"},
    install_requires=[
        "click",
        "Cython",
        "dash",
        "dash-core-components==0.21.0rc1",
        "dash-html-components",
        "dash-renderer",
        "dataclasses",
        "matplotlib",
        "numpy",
        'sqlalchemy',
    ],
    entry_points={
        "console_scripts": [
            "stellarisdashboard = stellarisdashboard.main:main",
            "stellarisdashboardcli = stellarisdashboard.cli:cli",
        ],
    },
)
