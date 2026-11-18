# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

root = Path(SPECPATH)
datas = [
    (str(root / 'config'), 'config'),
    (str(root / 'models' / 'models_manifest.json'), 'models'),
    (str(root / 'models' / 'PP-OCRv5_server_det_infer'), 'models/PP-OCRv5_server_det_infer'),
    (str(root / 'models' / 'PP-OCRv5_server_rec_infer'), 'models/PP-OCRv5_server_rec_infer'),
    (str(root / 'models' / 'PP-OCRv5_mobile_det_infer'), 'models/PP-OCRv5_mobile_det_infer'),
    (str(root / 'models' / 'PP-OCRv5_mobile_rec_infer'), 'models/PP-OCRv5_mobile_rec_infer'),
]
datas += collect_data_files('paddlex', include_py_files=False)
datas += collect_data_files('paddleocr', include_py_files=False)
binaries = collect_dynamic_libs('paddle')

datas += copy_metadata('paddlex') + copy_metadata('paddleocr') + copy_metadata('paddlepaddle')
for dist_name in ('imagesize', 'opencv-contrib-python', 'pyclipper', 'pypdfium2', 'python-bidi', 'shapely'):
    datas += copy_metadata(dist_name)

a = Analysis([str(root / 'run.py')], pathex=[str(root)], binaries=binaries, datas=datas,
    hiddenimports=['paddleocr', 'paddle', 'paddlex', 'yaml', 'openpyxl'], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='银行回单工具', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='BankReceiptTool_v0_3_10')
