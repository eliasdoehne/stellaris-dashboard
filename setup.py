"""
MIT License

Copyright (c) 2018 Elias Doehne

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from setuptools import setup

try:
    # Try to build the cython extension locally
    from Cython.Build import cythonize

    extension_modules = cythonize("stellarisdashboard/parsing/cython_ext/tokenizer.pyx")
except ImportError:
    print(
        "Cython is not installed, using pre-built C-extension if available, or (slow) fallback solution."
    )
    extension_modules = []
except RuntimeError as e:
    print(f"Warning: RuntimeError while building Cython extension: {e}")
    print("Using pre-built C-extension if available, or (slow) fallback solution.")
    extension_modules = []

with open("requirements.txt", "r") as f:
    install_requires = f.read().strip().split()

setup(
    name="stellarisdashboard",
    ext_modules=extension_modules,
    install_requires=install_requires,
    entry_points={
        "console_scripts": [
            "stellarisdashboard = stellarisdashboard.__main__:main",
            "stellarisdashboardcli = stellarisdashboard.cli:cli",
        ],
    },
)
