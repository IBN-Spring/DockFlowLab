# -*- mode: python ; coding: utf-8 -*-
import os


runtime_datas = []
if os.path.exists('runtime'):
    runtime_datas.append(('runtime', 'runtime'))

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        *runtime_datas,
        ('data', 'data'),                              # HTML 模板等
        ('openbabel_data', 'openbabel_data'),           # OpenBabel 力场数据
        ('logo.ico', '.'),                             # 图标
        ('favicon.ico', '.'),
        ('tools', 'tools'),
        # pdbfixer 数据文件（html/images 等）
        (r'C:\Users\39295\miniconda3\envs\DockFlowLab\Lib\site-packages\pdbfixer', 'pdbfixer'),
        # openmm app 数据文件（pdbNames.xml 等）
        (r'C:\Users\39295\miniconda3\envs\DockFlowLab\Lib\site-packages\openmm\app\data', 'openmm/app/data'),
        # OpenMM 平台插件
        (r'C:\Users\39295\miniconda3\envs\DockFlowLab\Library\lib\plugins', 'Library/lib/plugins'),
    ],
    hiddenimports=[
        # 验证模块依赖
        'wmi', 'rsa', 'ntplib',
        # 核心模块依赖
        'psutil', 'numpy', 'pandas', 'matplotlib',
        'matplotlib.backends.backend_agg',
        # PyQt5
        'PyQt5.sip', 'PyQt5.QtCore', 'PyQt5.QtGui', 'PyQt5.QtWidgets',
        # pdbfixer / openmm
        'pdbfixer', 'pdbfixer.pdbfixer',
        'openmm', 'openmm.app', 'openmm.app.pdbfile',
        'openmm.unit',
        # src 内部包（入口会自动收集直接导入；这里补充动态导入）
        'src', 'src.core', 'src.utils', 'src.Authentication',
        'src.core.config', 'src.core.logger', 'src.core.utils',
        'src.core.downloader', 'src.core.receptor_processor',
        'src.core.ligand_processor', 'src.core.box_generator',
        'src.core.docking', 'src.core.analyzer',
        'src.core.pymol_generator', 'src.core.report_generator',
        'src.core.pipeline',
        'src.utils.path', 'src.utils.cacheManager', 'src.utils.configManager',
        'src.Authentication.Authetication', 'src.Authentication.AutheticationUI',
        'src.Authentication.LicenseCache', 'src.Authentication.ValidatorConfig',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DockFlowLab',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['favicon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='DockFlowLab',
)
