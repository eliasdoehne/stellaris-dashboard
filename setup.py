from setuptools import setup, find_packages

try:
    # Try to build the cython extension locally
    from Cython.Build import cythonize

    extension_modules = cythonize("src/stellarisdashboard/cython_ext/token_value_stream.pyx")
except ImportError:
    print("Cython is not installed, using pre-built C-extension if available, or (slow) fallback solution.")
    extension_modules = []
except RuntimeError as e:
    print(f"Warning: RuntimeError while building Cython extension: {e}")
    print("Using pre-built C-extension if available, or (slow) fallback solution.")
    extension_modules = []

setup(
    name="stellarisdashboard",
    ext_modules=extension_modules,
    packages=find_packages("src"),
    package_dir={"": "src"},
    install_requires=[
        "click",
        "dash",
        "dash-core-components==0.21.0rc1",
        "dash-html-components",
        "dash-renderer",
        "dataclasses",
        "matplotlib",
        'sqlalchemy',
    ],
    entry_points={
        "console_scripts": [
            "stellarisdashboard = stellarisdashboard.__main__:main",
            "stellarisdashboardcli = stellarisdashboard.cli:cli",
        ],
    },
)
