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

a = Analysis(['stellarisdashboard/__main__.py'],
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
             noarchive=False)
pyz = PYZ(a.pure, a.zipped_data,
             cipher=block_cipher)
exe = EXE(pyz,
          a.scripts,
          [],
          exclude_binaries=True,
          name='stellarisdashboard',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          console=True )
coll = COLLECT(exe,
               a.binaries,
               a.zipfiles,
               a.datas,
               strip=False,
               upx=True,
               name='stellarisdashboard-win')
