# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# 번들링할 데이터/리소스 폴더 매핑: (실제 경로, 번들 내부 대상 폴더명)
APP_VERSION = os.environ.get('APP_VERSION', '1.0.0')

added_datas = [
    ('examples', 'examples'),
    ('models', 'models'),
]

if os.path.exists('.env'):
    added_datas.append(('.env', '.'))

for package in ('ultralytics', 'mediapipe', 'librosa'):
    added_datas += collect_data_files(package)

a = Analysis(
    ['app/main.py'],
    pathex=['.'],
    binaries=[],
    datas=added_datas,
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'google_auth_oauthlib',
        'google_auth_oauthlib.flow',
        'wsgiref.simple_server',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GuitarPracticeSupport',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI 전용(터미널 창 숨김) / 디버깅 시에는 True로 두고 확인 가능
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='GuitarPracticeSupport',
)

# macOS 애플리케이션 번들 (.app) 생성
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='GuitarPracticeSupport.app',
        icon=None,
        bundle_identifier='com.capstone.guitarpractice',
        version=APP_VERSION,
        info_plist={
            'CFBundleShortVersionString': APP_VERSION,
            'CFBundleVersion': APP_VERSION,
            'NSHighResolutionCapable': True,
            'NSCameraUsageDescription': '기타 연주 감지 및 피드백을 위해 카메라 권한이 필요합니다.',
            'NSMicrophoneUsageDescription': '기타 소리 분석을 위해 마이크 권한이 필요합니다.',
        }
    )
