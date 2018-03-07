from setuptools import setup, find_packages

try:
    from Cython.Build import cythonize

    extension_modules = cythonize("src/stellarisdashboard/cython_ext/token_value_stream.pyx")
except ImportError:
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
        "dash-core-components==0.13.0-rc4",
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
