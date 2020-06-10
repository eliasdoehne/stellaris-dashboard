# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

added_files = [
    ('env/Lib/site-packages/sqlalchemy', 'sqlalchemy'),
    ('env/Lib/site-packages/plotly/package_data', 'plotly/package_data'),
    ('env/Lib/site-packages/dash/favicon.ico', 'dash/favicon.ico'),
    ('env/Lib/site-packages/dash_core_components', 'dash_core_components'),
    ('env/Lib/site-packages/dash_html_components', 'dash_html_components'),
    ('env/Lib/site-packages/dash_renderer', 'dash_renderer'),
    ('./README.md', '.'),
    ('./stellarisdashboard/dashboard_app/static', 'stellarisdashboard/dashboard_app/static'),
    ('./stellarisdashboard/dashboard_app/templates', 'stellarisdashboard/dashboard_app/templates'),
    ('./stellarisdashboard/parsing/cython_ext', 'stellarisdashboard/parsing/cython_ext'),
]

a_main = Analysis(
    ['stellarisdashboard/__main__.py'],
    pathex=['.'],
    binaries=[],
    datas=added_files,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False
)


a_parse_saves = Analysis(
    ['stellarisdashboard\\parse_existing_saves.py'],
    pathex=['.'],
    binaries=[],
    datas=added_files,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False
)

MERGE(
    (a_main, 'stellarisdashboard', 'stellarisdashboard'),
    (a_parse_saves, 'parse-saves', 'parse-saves')
)

pyz_main = PYZ(a_main.pure, a_main.zipped_data,
             cipher=block_cipher)
exe_main = EXE(pyz_main,
          a_main.scripts,
          [],
          exclude_binaries=True,
          name='stellarisdashboard',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          console=True )


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
               name='stellarisdashboard-win')


