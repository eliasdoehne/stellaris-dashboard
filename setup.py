from distutils.core import setup
from Cython.Build import cythonize

setup(
    ext_modules=cythonize("src/token_value_stream.pyx"), requires=['Cython']
)
