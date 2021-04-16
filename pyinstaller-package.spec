# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import importlib


def module_path(module_name):
    module = importlib.import_module(module_name)
    return Path(module.__file__).resolve().parent


block_cipher = None
added_files = [
    (str(module_path("sqlalchemy")), "sqlalchemy/"),
    (str(module_path("plotly")), "plotly"),
    (str(module_path("dash") / "favicon.ico"), "dash/favicon.ico"),
    (str(module_path("dash_core_components")), "dash_core_components"),
    (str(module_path("dash_html_components")), "dash_html_components"),
    (str(module_path("dash_renderer")), "dash_renderer/"),
    (str(Path("README.md")), "./"),
    (str(Path("stellarisdashboard/dashboard_app/assets")), "stellarisdashboard/dashboard_app/assets/"),
    (str(Path("stellarisdashboard/dashboard_app/templates")), "stellarisdashboard/dashboard_app/templates/"),
    (str(Path("stellarisdashboard/parsing/cython_ext")), "stellarisdashboard/parsing/cython_ext/"),
]
print(added_files)

a_main = Analysis(
    [str(Path('stellarisdashboard/__main__.py'))],
    pathex=['.'],
    binaries=[],
    datas=added_files,
    hiddenimports=["sqlalchemy.sql.default_comparator"],
    hookspath=["pyinstaller-hooks"],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False
)

a_parse_saves = Analysis(
    [str(Path('stellarisdashboard/parse_existing_saves.py'))],
    pathex=['.'],
    binaries=[],
    datas=added_files,
    hiddenimports=["sqlalchemy.sql.default_comparator"],
    hookspath=["pyinstaller-hooks"],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False
)

MERGE(
    (a_main, 'dashboard', 'dashboard'),
    (a_parse_saves, 'parse-saves', 'parse-saves')
)

pyz_main = PYZ(a_main.pure, a_main.zipped_data,
               cipher=block_cipher)
exe_main = EXE(pyz_main,
               a_main.scripts,
               [],
               exclude_binaries=True,
               name='dashboard',
               debug=False,
               bootloader_ignore_signals=False,
               strip=False,
               upx=True,
               console=True)

pyz_parse_saves = PYZ(
    a_parse_saves.pure,
    a_parse_saves.zipped_data,
    cipher=block_cipher
)
parse_saves_exe = EXE(
    pyz_parse_saves,
    a_parse_saves.scripts,
    [],
    exclude_binaries=True,
    name='parse_saves',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True
)

coll = COLLECT(exe_main,
               a_main.binaries,
               a_main.zipfiles,
               a_main.datas,
               parse_saves_exe,
               a_parse_saves.binaries,
               a_parse_saves.zipfiles,
               a_parse_saves.datas,
               strip=False,
               upx=True,
               upx_exclude=[],
               name='stellarisdashboard-build')
