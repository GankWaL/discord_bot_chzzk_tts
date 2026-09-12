# -*- mode: python ; coding: utf-8 -*-
# 설치 파일용 빌드: tts_bot_gui.exe(컨트롤 패널)와 tts_bot.exe(봇)가 _internal 을 공유하는 폴더 하나.
# installer\build.ps1 이 실행한다.
import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.dirname(SPECPATH)
SRC = os.path.join(ROOT, "src")
ICON = os.path.join(ROOT, "icon", "icon.ico")

datas, binaries, hiddenimports = [], [], ["_cffi_backend"]
for pkg in ("discord", "nacl", "davey"):  # 음성(암호화·DAVE) 모듈은 자동 탐지가 안 된다
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

bot = Analysis([os.path.join(SRC, "bot.py")], pathex=[SRC], binaries=binaries, datas=datas,
               hiddenimports=hiddenimports)
gui = Analysis([os.path.join(SRC, "bot_gui.py")], pathex=[SRC])

bot_exe = EXE(PYZ(bot.pure), bot.scripts, [], exclude_binaries=True, name="tts_bot",
              console=True, icon=ICON)
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="tts_bot_gui",
              console=False, icon=ICON)

coll = COLLECT(gui_exe, gui.binaries, gui.datas, bot_exe, bot.binaries, bot.datas, name="DiscordTTSBot")
