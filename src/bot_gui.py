"""디스코드 TTS 봇 컨트롤 패널 (Windows GUI).

- 봇 실행 / 종료 / 재시작 버튼, bot.log 실시간 확인
- [설정] 서브 창에서 토큰·API 키 입력 → .env 자동 생성
- [환경 설치] 버튼으로 requirements.txt 라이브러리 일괄 설치 (저장소 실행 시에만)
- [exe 재빌드] 버튼으로 패치 후 exe 재빌드 (빌드 환경 자동 구성, 저장소 실행 시에만)
- [업데이트 확인]: 설치 마법사로 설치한 exe 는 GitHub 릴리스로, 저장소 실행은 git pull 로 업데이트
- 창을 닫으면 트레이로 최소화, 트레이 우클릭 메뉴로 제어
- `--autostart` 옵션으로 실행하면 GUI가 뜨면서 봇도 자동 실행 (시작 프로그램용)

갓 클론한 상태(라이브러리 미설치)에서도 표준 라이브러리만으로 실행 가능:
    python src\\bot_gui.py
"""

import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from tkinter import messagebox, ttk

try:
    import pystray
    from PIL import Image, ImageDraw

    HAS_TRAY = True
except ImportError:  # 최초 설치 전에는 트레이 없이 동작
    pystray = None
    HAS_TRAY = False

from app_version import VERSION as APP_VERSION

# 프로젝트 루트: exe(PyInstaller)면 실행 파일 위치, 아니면 src/ 의 상위 폴더
FROZEN = bool(getattr(sys, "frozen", False))
BASE_DIR = (
    os.path.dirname(sys.executable)
    if FROZEN
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
# exe는 dist\ 안에 있으므로 루트(레포)는 한 단계 위일 수 있다
if FROZEN and os.path.basename(BASE_DIR).lower() == "dist":
    REPO_DIR = os.path.dirname(BASE_DIR)
else:
    REPO_DIR = BASE_DIR

# 설치 마법사로 설치한 exe (저장소 밖) — 업데이트를 git 대신 GitHub 릴리스로 받는다
INSTALLED = FROZEN and not os.path.isdir(os.path.join(REPO_DIR, ".git"))
LATEST_RELEASE_API = "https://api.github.com/repos/GankWaL/discord_bot_chzzk_tts/releases/latest"

BOT_SCRIPT = os.path.join(REPO_DIR, "src", "bot.py")
BOT_EXE = os.path.join(BASE_DIR, "tts_bot.exe")
GUI_EXE = os.path.join(REPO_DIR, "dist", "tts_bot_gui.exe")
LOG_FILE = os.path.join(BASE_DIR, "bot.log")
ENV_FILE = os.path.join(BASE_DIR, ".env")
REQUIREMENTS = os.path.join(REPO_DIR, "requirements.txt")
BUILD_ENV_PY = os.path.join(REPO_DIR, "build_env", "Scripts", "python.exe")
ICON_PNG = os.path.join(REPO_DIR, "icon", "icon.png")
ICON_ICO = os.path.join(REPO_DIR, "icon", "icon.ico")
NO_WINDOW = subprocess.CREATE_NO_WINDOW
MAX_LOG_LINES = 2000

# 단일 인스턴스 잠금용 로컬 포트 (다른 앱과 겹치지 않도록 배너로 검증)
SINGLETON_HOST = "127.0.0.1"
SINGLETON_PORT = 51765
SINGLETON_BANNER = b"TTSBOTGUI"


def try_acquire_singleton() -> socket.socket | None:
    """포트를 선점해 단일 인스턴스 잠금을 얻는다. 실패하면 None (이미 실행 중)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((SINGLETON_HOST, SINGLETON_PORT))
        sock.listen(1)
        return sock
    except OSError:
        sock.close()
        return None


def notify_existing_instance(autostart: bool) -> bool:
    """이미 실행 중인 GUI에 창을 띄우라고 요청한다. 성공 여부를 반환."""
    try:
        with socket.create_connection((SINGLETON_HOST, SINGLETON_PORT), timeout=2) as conn:
            conn.settimeout(2)
            if conn.recv(16) != SINGLETON_BANNER:
                return False  # 우리 GUI가 아닌 다른 앱이 포트를 쓰는 중
            conn.sendall(b"show autostart" if autostart else b"show")
            return True
    except OSError:
        return False

ENV_FIELDS = [
    ("DISCORD_TOKEN", "디스코드 봇 토큰 (필수)"),
    ("TYPECAST_API_KEY", "Typecast API 키 (선택)"),
    ("GOOGLE_TTS_API_KEY", "Google TTS API 키 (선택)"),
]

BUILD_BOT_ARGS = [
    "--noconfirm", "--onefile",
    "--icon", os.path.join("icon", "icon.ico"),
    "--name", "tts_bot",
    "--collect-all", "discord",
    "--collect-all", "nacl",
    "--collect-all", "davey",
    "--hidden-import", "_cffi_backend",
    os.path.join("src", "bot.py"),
]
BUILD_GUI_ARGS = [
    "--noconfirm", "--onefile", "--windowed",
    "--icon", os.path.join("icon", "icon.ico"),
    os.path.join("src", "bot_gui.py"),
]


def read_env() -> dict:
    """ .env 를 dict 로 읽는다 (없으면 빈 dict)."""
    values = {}
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip()
    except OSError:
        pass
    return values


def write_env(new_values: dict) -> None:
    """기존 .env 값을 유지하면서 전달된 키만 갱신해 저장한다.

    exe 모드(dist\\.env)와 소스 모드(루트 .env)가 어긋나지 않도록
    두 위치가 다르면 같은 내용으로 함께 저장한다.
    """
    values = read_env()
    values.update(new_values)
    lines = ["# 디스코드 TTS 봇 설정 (GUI 설정 창에서 관리됨)"]
    for key, val in values.items():
        lines.append(f"{key}={val}")
    content = "\n".join(lines) + "\n"

    targets = {ENV_FILE, os.path.join(REPO_DIR, ".env")}
    for target in targets:
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)


def find_bot_pids() -> list:
    """bot.py / tts_bot.exe 를 실행 중인 프로세스 PID 목록."""
    cmd = (
        "Get-CimInstance Win32_Process -Filter "
        "\"Name='python.exe' or Name='pythonw.exe' or Name='tts_bot.exe'\" "
        "| Where-Object { $_.CommandLine -match 'bot\\.py|tts_bot\\.exe' } "
        "| Select-Object -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=NO_WINDOW,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(line) for line in out.split() if line.strip().isdigit()]


def find_tts_server_pids() -> list:
    """커스텀 TTS 서버 프로세스 PID 목록."""
    cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" "
        "| Where-Object { $_.CommandLine -match 'custom_tts_server' } "
        "| Select-Object -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=NO_WINDOW,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(line) for line in out.split() if line.strip().isdigit()]


TTS_SERVER_PY = os.path.join(REPO_DIR, "tts_env", "Scripts", "python.exe")
TTS_SERVER_SCRIPT = os.path.join(REPO_DIR, "src", "custom_tts_server.py")
TTS_SERVER_LOG = os.path.join(REPO_DIR, "tts_server.log")


def tts_env_ready() -> bool:
    return os.path.exists(TTS_SERVER_PY) and os.path.exists(TTS_SERVER_SCRIPT)


def has_custom_voices() -> bool:
    models = os.path.join(REPO_DIR, "my_voice_models")
    if os.path.isdir(models):
        for name in os.listdir(models):
            if os.path.isfile(os.path.join(models, name, "sovits.pth")):
                return True
    datasets = os.path.join(REPO_DIR, "my_voice")
    if os.path.isdir(datasets):
        for name in os.listdir(datasets):
            meta = os.path.join(datasets, name, "metadata.csv")
            if os.path.isfile(meta) and os.path.getsize(meta) > 0:
                return True
    return False


def tts_server_running() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 51770), timeout=1):
            return True
    except OSError:
        return False


def system_python() -> str | None:
    """시스템 파이썬 경로 (소스 실행 시엔 자기 자신)."""
    if not FROZEN:
        return sys.executable
    return shutil.which("python")


def parse_version(text: str) -> tuple:
    """'v0.1.2' / '0.1.2' → (0, 1, 2)"""
    return tuple(int(n) for n in re.findall(r"\d+", text))


def fetch_latest_release() -> dict | None:
    """GitHub 최신 릴리스 정보. 릴리스가 없으면 None, 네트워크 오류는 예외로 올린다."""
    req = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "discord-tts-bot"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def make_tray_image():
    if os.path.exists(ICON_PNG):
        return Image.open(ICON_PNG)
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill="#5865F2")
    draw.polygon([(24, 20), (24, 44), (44, 32)], fill="white")
    return img


class EnvDialog(tk.Toplevel):
    """토큰/API 키를 입력받아 .env 를 생성·수정하는 서브 창."""

    def __init__(self, parent: tk.Tk, on_saved=None):
        super().__init__(parent)
        self.on_saved = on_saved
        self.title("설정 — 토큰 / API 키")
        self.resizable(False, False)
        self.grab_set()

        current = read_env()
        self.entries = {}

        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text="봇 실행에 필요한 키를 입력하세요. 저장하면 .env 파일이 생성/수정됩니다.",
            font=("맑은 고딕", 9),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        for i, (key, label) in enumerate(ENV_FIELDS, start=1):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="w", pady=3)
            entry = ttk.Entry(frame, width=52)
            entry.insert(0, current.get(key, ""))
            entry.grid(row=i, column=1, sticky="we", pady=3, padx=(8, 0))
            self.entries[key] = entry

        ttk.Label(
            frame,
            text="※ 선택 항목은 비워두면 해당 엔진 목소리가 비활성화됩니다.\n"
            "※ 봇이 실행 중이면 재시작해야 적용됩니다.",
            foreground="#888888",
            font=("맑은 고딕", 8),
        ).grid(row=len(ENV_FIELDS) + 1, column=0, columnspan=2, sticky="w", pady=(10, 8))

        btns = ttk.Frame(frame)
        btns.grid(row=len(ENV_FIELDS) + 2, column=0, columnspan=2, sticky="e")
        ttk.Button(btns, text="저장", command=self._save).pack(side="right", padx=2)
        ttk.Button(btns, text="취소", command=self.destroy).pack(side="right", padx=2)

    def _save(self) -> None:
        values = {key: entry.get().strip() for key, entry in self.entries.items()}
        if not values["DISCORD_TOKEN"]:
            messagebox.showwarning("설정", "디스코드 봇 토큰은 필수입니다.", parent=self)
            return
        try:
            write_env(values)
        except OSError as e:
            messagebox.showerror("설정", f".env 저장 실패: {e}", parent=self)
            return
        if self.on_saved:
            self.on_saved()
        self.destroy()


class BotGui:
    def __init__(self, root: tk.Tk, autostart: bool = False, lock_sock: socket.socket | None = None):
        self.root = root
        self.proc: subprocess.Popen | None = None
        self.log_handle = None
        self.log_queue: queue.Queue = queue.Queue()
        self.busy = False
        self.tray = None
        self.lock_sock = lock_sock

        root.title("디스코드 TTS 봇 컨트롤 패널")
        if os.path.exists(ICON_ICO):
            try:
                root.iconbitmap(ICON_ICO)
            except tk.TclError:
                pass
        root.geometry("800x560")
        root.minsize(600, 400)

        # ---- 1행: 상태 + 봇 제어 버튼 ----
        top = ttk.Frame(root, padding=(8, 8, 8, 2))
        top.pack(fill="x")

        self.status_label = ttk.Label(top, text="상태 확인 중...", font=("맑은 고딕", 11, "bold"))
        self.status_label.pack(side="left")

        self.restart_btn = ttk.Button(top, text="재시작", command=lambda: self.run_action("restart"))
        self.restart_btn.pack(side="right", padx=2)
        self.stop_btn = ttk.Button(top, text="종료", command=lambda: self.run_action("stop"))
        self.stop_btn.pack(side="right", padx=2)
        self.start_btn = ttk.Button(top, text="실행", command=lambda: self.run_action("start"))
        self.start_btn.pack(side="right", padx=2)

        # ---- 2행: 도구 버튼 ----
        tools = ttk.Frame(root, padding=(8, 2, 8, 6))
        tools.pack(fill="x")

        self.env_btn = ttk.Button(tools, text="설정 (토큰/API 키)", command=self.open_settings)
        self.env_btn.pack(side="left", padx=2)
        self.install_btn = ttk.Button(tools, text="환경 설치 (라이브러리)", command=self.install_deps)
        self.build_btn = ttk.Button(tools, text="exe 재빌드", command=self.rebuild)
        if not INSTALLED:  # 설치본은 라이브러리가 exe 에 들어 있고 빌드할 소스도 없다
            self.install_btn.pack(side="left", padx=2)
            self.build_btn.pack(side="left", padx=2)

        self.voice_btn = ttk.Button(tools, text="내 목소리 만들기", command=self.open_voice_studio)
        self.voice_btn.pack(side="left", padx=2)

        self.tts_server_btn = ttk.Button(tools, text="커스텀 TTS 서버", command=self.start_tts_server)
        self.tts_server_btn.pack(side="left", padx=2)

        self.update_btn = ttk.Button(tools, text="업데이트 확인", command=self.check_update)
        self.update_btn.pack(side="right", padx=2)

        mode = "설치본" if INSTALLED else ("exe" if FROZEN else "소스")
        tray_note = "" if HAS_TRAY else " · 트레이 비활성(환경 설치 필요)"
        ttk.Label(
            tools,
            text=f"v{APP_VERSION} · 실행 모드: {mode}{tray_note}",
            foreground="#888888",
            font=("맑은 고딕", 8),
        ).pack(side="right", padx=(0, 8))

        # ---- 로그 뷰 ----
        log_frame = ttk.Frame(root, padding=(8, 0, 8, 8))
        log_frame.pack(fill="both", expand=True)

        self.text = tk.Text(
            log_frame,
            state="disabled",
            wrap="none",
            font=("Consolas", 9),
            background="#1e1e1e",
            foreground="#d4d4d4",
            insertbackground="#d4d4d4",
        )
        scroll_y = ttk.Scrollbar(log_frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll_y.set)
        scroll_y.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)

        if HAS_TRAY:
            self._setup_tray()
        threading.Thread(target=self._tail_log_loop, daemon=True).start()
        threading.Thread(target=self._status_loop, daemon=True).start()
        threading.Thread(target=self._cleanup_old_exe, daemon=True).start()
        threading.Thread(target=self._auto_start_tts_server, daemon=True).start()
        if self.lock_sock:
            threading.Thread(target=self._singleton_listener, daemon=True).start()
        self._poll_log_queue()

        root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 토큰 미설정이면 설정 창을 먼저 띄운다 (최초 설치 흐름)
        if not read_env().get("DISCORD_TOKEN"):
            self.log("[안내] 디스코드 봇 토큰이 설정되어 있지 않습니다. 설정 창을 확인하세요.")
            root.after(300, self.open_settings)
        elif autostart:
            self.run_action("start")

    # ---------- 공용 ----------

    def log(self, message: str) -> None:
        self.log_queue.put(message.rstrip("\n") + "\n")

    def _set_all_buttons(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for b in (self.start_btn, self.stop_btn, self.restart_btn,
                  self.install_btn, self.build_btn, self.update_btn):
            b.configure(state=state)

    def _ask_on_main(self, title: str, message: str) -> bool:
        """작업 스레드에서 메인 스레드의 확인 창 결과를 받아온다."""
        result = {"ok": False}
        done = threading.Event()

        def ask():
            result["ok"] = messagebox.askyesno(title, message)
            done.set()

        self.root.after(0, ask)
        done.wait()
        return result["ok"]

    def _stream_cmd(self, cmd: list, prefix: str, cwd: str = REPO_DIR) -> int:
        """명령을 실행하고 출력을 로그 창으로 흘려보낸다. 반환값은 종료 코드."""
        self.log(f"{prefix} $ {' '.join(os.path.basename(cmd[0]).split())} {' '.join(cmd[1:])}")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=NO_WINDOW,
            )
        except OSError as e:
            self.log(f"{prefix} 실행 실패: {e}")
            return -1
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                self.log(f"{prefix} {line}")
        return proc.wait()

    def _singleton_listener(self) -> None:
        """두 번째 실행 시도가 들어오면 기존 창을 앞으로 가져온다."""
        while True:
            try:
                conn, _ = self.lock_sock.accept()
            except OSError:  # 소켓이 닫힘 (재시작 등)
                return
            with conn:
                try:
                    conn.sendall(SINGLETON_BANNER)
                    conn.settimeout(2)
                    data = conn.recv(32)
                except OSError:
                    continue
            if not data.startswith(b"show"):
                continue
            self._tray_show()
            # 시작 프로그램 등에서 --autostart 로 재실행됐다면 봇도 켜준다
            if b"autostart" in data:
                own = self.proc is not None and self.proc.poll() is None
                if not self.busy and not own and not find_bot_pids():
                    self.run_action("start")

    def _cleanup_old_exe(self) -> None:
        """재빌드 재시작 후 남은 이전 GUI exe(_old)를 정리한다."""
        old = os.path.join(REPO_DIR, "dist", "tts_bot_gui_old.exe")
        for _ in range(10):
            if not os.path.exists(old):
                return
            try:
                os.remove(old)
                return
            except OSError:  # 이전 프로세스가 아직 종료 중
                time.sleep(1)

    # ---------- 내 목소리 만들기 ----------

    def open_voice_studio(self) -> None:
        try:
            import voice_studio
        except ImportError:
            messagebox.showinfo(
                "내 목소리 만들기",
                "녹음 기능에 필요한 라이브러리가 없습니다.\n[환경 설치] 버튼으로 설치 후 GUI를 다시 실행해주세요.",
            )
            return
        voice_studio.VoiceStudio(self.root, REPO_DIR)

    def _auto_start_tts_server(self) -> None:
        """GUI 시작 시 커스텀 TTS 서버를 자동으로 미리 띄운다 (조건 충족 시)."""
        if not tts_env_ready() or not has_custom_voices():
            return
        if tts_server_running() or find_tts_server_pids():
            self.log("[TTS 서버] 이미 실행 중입니다.")
            return
        try:
            log = open(TTS_SERVER_LOG, "w", encoding="utf-8")
            subprocess.Popen(
                [TTS_SERVER_PY, TTS_SERVER_SCRIPT],
                cwd=REPO_DIR,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=NO_WINDOW,
            )
            self.log("[TTS 서버] 자동 시작했습니다 (모델 로딩까지 수십 초, 로그: tts_server.log)")
        except OSError as e:
            self.log(f"[TTS 서버] 자동 시작 실패: {e}")

    def start_tts_server(self) -> None:
        """수동 시작 버튼 — 자동 시작이 안 된 경우(환경 방금 구성 등)에 사용."""
        if tts_server_running() or find_tts_server_pids():
            messagebox.showinfo("커스텀 TTS 서버", "서버가 이미 실행 중입니다.")
            return
        if not tts_env_ready():
            if messagebox.askyesno(
                "커스텀 TTS 서버",
                "추론 환경이 없습니다. 지금 구성할까요?\n"
                "(bat\\setup_tts_server.bat 실행 — Python·git 필요, 수 GB 다운로드)",
            ):
                os.startfile(os.path.join(REPO_DIR, "bat", "setup_tts_server.bat"))
            return
        self._auto_start_tts_server()

    # ---------- 설정 (.env) ----------

    def open_settings(self) -> None:
        EnvDialog(self.root, on_saved=lambda: self.log("[설정] .env 저장 완료. 봇 실행 중이면 재시작해야 적용됩니다."))

    # ---------- 환경 설치 ----------

    def install_deps(self) -> None:
        if self.busy:
            return
        py = system_python()
        if not py:
            messagebox.showerror("환경 설치", "시스템에 python이 없습니다. https://python.org 에서 설치 후 다시 시도하세요.")
            return
        self.busy = True
        self._set_all_buttons(False)
        threading.Thread(target=self._do_install, args=(py,), daemon=True).start()

    def _do_install(self, py: str) -> None:
        try:
            self.log("[설치] 라이브러리 설치를 시작합니다...")
            code = self._stream_cmd([py, "-m", "pip", "install", "-r", REQUIREMENTS], "[설치]")
            if code == 0:
                self.log("[설치] 완료! 트레이 기능은 GUI를 다시 실행하면 활성화됩니다.")
            else:
                self.log(f"[설치] 실패 (종료 코드 {code}). 로그를 확인하세요.")
        finally:
            self.busy = False
            self.root.after(0, lambda: self._set_all_buttons(True))
            self._refresh_status()

    # ---------- exe 재빌드 ----------

    def rebuild(self) -> None:
        if self.busy:
            return
        if not messagebox.askyesno(
            "exe 재빌드",
            "봇을 종료하고 exe를 다시 빌드합니다. 몇 분 걸릴 수 있습니다.\n계속할까요?",
        ):
            return
        self.busy = True
        self._set_all_buttons(False)
        threading.Thread(target=self._do_rebuild, daemon=True).start()

    def _do_rebuild(self) -> None:
        try:
            self._stop_bot()
            py = system_python()
            if not py:
                self.log("[빌드] 시스템 python을 찾을 수 없습니다.")
                return

            # 1) 빌드 전용 가상환경 준비
            if not os.path.exists(BUILD_ENV_PY):
                self.log("[빌드] 빌드 환경(build_env)을 처음 구성합니다...")
                if self._stream_cmd([py, "-m", "venv", os.path.join(REPO_DIR, "build_env")], "[빌드]") != 0:
                    self.log("[빌드] 가상환경 생성 실패")
                    return
                if self._stream_cmd(
                    [BUILD_ENV_PY, "-m", "pip", "install", "-q", "-r", REQUIREMENTS, "pyinstaller"],
                    "[빌드]",
                ) != 0:
                    self.log("[빌드] 빌드 의존성 설치 실패")
                    return

            # 2) 봇 exe 빌드
            self.log("[빌드] tts_bot.exe 빌드 중...")
            if self._stream_cmd([BUILD_ENV_PY, "-m", "PyInstaller", *BUILD_BOT_ARGS], "[빌드]") != 0:
                self.log("[빌드] tts_bot.exe 빌드 실패")
                return

            # 3) GUI exe 빌드 — 실행 중인 자신은 덮어쓸 수 없지만 이름 변경은 가능하므로
            #    자신을 _old 로 비켜두고 새 exe를 원래 이름으로 바로 빌드한다
            self_locked = False
            if FROZEN:
                try:
                    self_locked = os.path.samefile(sys.executable, GUI_EXE)
                except OSError:
                    self_locked = False
            renamed_old = None
            gui_name = "tts_bot_gui"
            if self_locked:
                old_path = os.path.join(REPO_DIR, "dist", "tts_bot_gui_old.exe")
                try:
                    if os.path.exists(old_path):
                        os.remove(old_path)
                    os.rename(GUI_EXE, old_path)
                    renamed_old = old_path
                except OSError:
                    gui_name = "tts_bot_gui_new"  # 이름 변경 실패 시 예전 방식으로

            self.log(f"[빌드] {gui_name}.exe 빌드 중...")
            if self._stream_cmd(
                [BUILD_ENV_PY, "-m", "PyInstaller", "--name", gui_name, *BUILD_GUI_ARGS],
                "[빌드]",
            ) != 0:
                self.log("[빌드] GUI exe 빌드 실패")
                if renamed_old and not os.path.exists(GUI_EXE):
                    try:
                        os.rename(renamed_old, GUI_EXE)  # 실패 시 원상 복구
                    except OSError:
                        pass
                return

            # 4) dist 에 설정/아이콘 복사
            dist = os.path.join(REPO_DIR, "dist")
            os.makedirs(os.path.join(dist, "icon"), exist_ok=True)
            dist_env = os.path.join(dist, ".env")
            if os.path.exists(ENV_FILE) and os.path.abspath(ENV_FILE) != os.path.abspath(dist_env):
                shutil.copy2(ENV_FILE, dist_env)
            for name in ("icon.png", "icon.ico"):
                src_path = os.path.join(REPO_DIR, "icon", name)
                if os.path.exists(src_path):
                    shutil.copy2(src_path, os.path.join(dist, "icon", name))

            self.log("[빌드] 완료!")
            if renamed_old:
                if self._ask_on_main(
                    "재빌드 완료",
                    "새 컨트롤 패널이 준비되었습니다. 지금 재시작해서 적용할까요?\n"
                    "(봇도 자동으로 다시 시작됩니다)",
                ):
                    # 새 인스턴스가 단일 인스턴스 잠금을 얻을 수 있도록 먼저 해제
                    if self.lock_sock:
                        try:
                            self.lock_sock.close()
                        except OSError:
                            pass
                        self.lock_sock = None
                    subprocess.Popen(
                        [GUI_EXE, "--autostart"],
                        cwd=os.path.dirname(GUI_EXE),
                    )
                    if self.tray:
                        self.tray.stop()
                    self.root.after(0, self.root.destroy)
                else:
                    self.log("[빌드] 다음에 GUI를 다시 실행하면 새 버전이 적용됩니다.")
            elif gui_name == "tts_bot_gui_new":
                self.log("[빌드] 새 GUI는 tts_bot_gui_new.exe 로 저장했습니다. "
                         "start_gui.bat 로 다시 실행하면 자동으로 교체됩니다.")
        finally:
            self.busy = False
            self.root.after(0, lambda: self._set_all_buttons(True))
            self._refresh_status()

    # ---------- 업데이트 확인 ----------

    def check_update(self) -> None:
        if self.busy:
            return
        if INSTALLED:
            target = self._do_check_release
        else:
            if not shutil.which("git"):
                messagebox.showerror("업데이트", "git 이 설치되어 있지 않아 업데이트를 확인할 수 없습니다.")
                return
            target = self._do_check_update
        self.busy = True
        self._set_all_buttons(False)
        threading.Thread(target=target, daemon=True).start()

    def _git(self, *args: str) -> tuple:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=REPO_DIR,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                creationflags=NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return -1, str(e)
        return proc.returncode, (proc.stdout + proc.stderr).strip()

    def _do_check_update(self) -> None:
        rebuild_after = False
        try:
            self.log("[업데이트] 깃허브에서 새 커밋을 확인하는 중...")
            code, out = self._git("fetch", "--quiet")
            if code != 0:
                self.log(f"[업데이트] 원격 확인 실패: {out}")
                return
            _, branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
            code, count = self._git("rev-list", f"HEAD..origin/{branch}", "--count")
            if code != 0:
                self.log(f"[업데이트] 비교 실패: {count}")
                return
            try:
                n = int(count.split()[-1])
            except (ValueError, IndexError):
                self.log(f"[업데이트] 커밋 수 확인 실패: {count}")
                return
            if n == 0:
                self.log("[업데이트] 이미 최신 상태입니다.")
                self.root.after(0, lambda: messagebox.showinfo("업데이트", "이미 최신 상태입니다."))
                return

            _, commits = self._git("log", f"HEAD..origin/{branch}", "--oneline", "-n", "10")
            self.log(f"[업데이트] 새 커밋 {n}개 발견:\n{commits}")
            if not self._ask_on_main(
                "업데이트",
                f"새 업데이트 {n}개가 있습니다:\n\n{commits}\n\n"
                "지금 업데이트하고 exe를 재빌드할까요? (봇이 재시작됩니다)",
            ):
                self.log("[업데이트] 사용자가 업데이트를 취소했습니다.")
                return

            code, out = self._git("pull", "--ff-only")
            self.log(f"[업데이트] {out}")
            if code != 0:
                self.log("[업데이트] git pull 실패. 로컬 변경사항이 있는지 확인하세요.")
                return
            rebuild_after = True
        finally:
            if not rebuild_after:
                self.busy = False
                self.root.after(0, lambda: self._set_all_buttons(True))
                self._refresh_status()

        # 업데이트 성공 시 이어서 재빌드 (busy 상태 유지한 채 진행)
        self.log("[업데이트] 업데이트 완료. exe 재빌드를 시작합니다...")
        self._do_rebuild()

    def _do_check_release(self) -> None:
        """설치본 업데이트: 최신 릴리스의 설치 파일을 받아 조용히 재설치한다.

        설치 파일이 끝나면 컨트롤 패널을 --autostart 로 다시 띄워 봇도 이어서 켜진다.
        """
        installing = False
        try:
            self.log("[업데이트] 깃허브 릴리스를 확인하는 중...")
            try:
                release = fetch_latest_release()
            except (OSError, ValueError) as e:
                self.log(f"[업데이트] 확인 실패: {e}")
                return
            if release is None:
                self.log("[업데이트] 아직 배포된 릴리스가 없습니다.")
                return
            tag = release.get("tag_name", "")
            latest = release.get("name") or tag  # 릴리스 제목(v0.0.3)을 보여 준다
            if parse_version(tag) <= parse_version(APP_VERSION):
                self.log(f"[업데이트] 이미 최신 버전입니다 (v{APP_VERSION}).")
                self.root.after(0, lambda: messagebox.showinfo("업데이트", "이미 최신 버전입니다."))
                return
            asset = next(
                (a for a in release.get("assets", []) if a.get("name", "").lower().endswith(".exe")),
                None,
            )
            if asset is None:
                self.log(f"[업데이트] {latest} 릴리스에 설치 파일이 없습니다.")
                return
            notes = (release.get("body") or "").strip()
            if len(notes) > 600:
                notes = notes[:600] + " ..."
            if not self._ask_on_main(
                "업데이트",
                f"새 버전 {latest} 이 있습니다 (현재 v{APP_VERSION}).\n\n{notes}\n\n"
                "지금 설치할까요? 봇이 잠시 종료되고, 설치가 끝나면 자동으로 다시 시작됩니다.",
            ):
                self.log("[업데이트] 사용자가 업데이트를 취소했습니다.")
                return

            setup = os.path.join(tempfile.gettempdir(), asset["name"])
            self.log(f"[업데이트] 설치 파일 다운로드 중... ({asset.get('size', 0) / 1e6:.0f} MB)")
            try:
                urllib.request.urlretrieve(asset["browser_download_url"], setup)
            except OSError as e:
                self.log(f"[업데이트] 다운로드 실패: {e}")
                return
            self._stop_bot()
            self.log("[업데이트] 설치를 시작합니다. 끝나면 컨트롤 패널이 다시 열립니다.")
            subprocess.Popen([setup, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
            installing = True
            self._exit_for_update()
        finally:
            if not installing:
                self.busy = False
                self.root.after(0, lambda: self._set_all_buttons(True))
                self._refresh_status()

    def _exit_for_update(self) -> None:
        """설치 파일이 exe 를 덮어쓰고 새 컨트롤 패널이 단일 인스턴스 잠금을 얻을 수 있게 종료한다."""
        if self.lock_sock:
            try:
                self.lock_sock.close()
            except OSError:
                pass
            self.lock_sock = None
        if self.tray:
            self.tray.stop()
        self.root.after(0, self.root.destroy)

    # ---------- 트레이 ----------

    def _setup_tray(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("창 열기", self._tray_show, default=True),
            pystray.MenuItem("봇 재시작", self._tray_restart),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("컨트롤 패널 종료 (봇은 유지)", self._tray_quit),
            pystray.MenuItem("완전 종료 (봇도 종료)", self._tray_quit_all),
        )
        self.tray = pystray.Icon("discord_tts_bot", make_tray_image(), "디스코드 TTS 봇", menu)
        self.tray.run_detached()

    def _on_close(self) -> None:
        if HAS_TRAY:
            self.root.withdraw()
            return
        # 트레이가 없으면 그냥 닫는다 (봇은 계속 실행됨을 안내)
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno("종료", "봇은 백그라운드에서 계속 실행됩니다. 창을 닫을까요?"):
                return
        self.root.destroy()

    def _tray_show(self, icon=None, item=None) -> None:
        self.root.after(0, self._show_window)

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _tray_restart(self, icon=None, item=None) -> None:
        self.run_action("restart")

    def _tray_quit(self, icon=None, item=None) -> None:
        if self.tray:
            self.tray.stop()
        self.root.after(0, self.root.destroy)

    def _tray_quit_all(self, icon=None, item=None) -> None:
        threading.Thread(target=self._quit_all, daemon=True).start()

    def _quit_all(self) -> None:
        try:
            self._stop_bot()
        except Exception:
            pass
        if self.tray:
            self.tray.stop()
        self.root.after(0, self.root.destroy)

    # ---------- 봇 제어 ----------

    def run_action(self, action: str) -> None:
        if self.busy:
            return
        self.busy = True
        self._set_all_buttons(False)
        threading.Thread(target=self._do_action, args=(action,), daemon=True).start()

    def _do_action(self, action: str) -> None:
        try:
            if action in ("stop", "restart"):
                self._stop_bot()
            if action in ("start", "restart"):
                self._start_bot()
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("오류", str(e)))
        finally:
            self.busy = False
            self.root.after(0, lambda: self._set_all_buttons(True))
            self._refresh_status()

    def _start_bot(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        if find_bot_pids():
            return  # 이미 외부에서 실행 중
        if not read_env().get("DISCORD_TOKEN"):
            self.root.after(0, self.open_settings)
            raise RuntimeError("디스코드 봇 토큰이 설정되지 않았습니다. 설정 창에서 입력해주세요.")

        if os.path.exists(BOT_EXE):
            bot_cmd = [BOT_EXE]
        else:
            py = system_python()
            if py is None or not os.path.exists(BOT_SCRIPT):
                raise FileNotFoundError("tts_bot.exe 도 없고 소스 실행 환경도 없습니다. [exe 재빌드] 를 실행해주세요.")
            bot_cmd = [py, BOT_SCRIPT]
        env = {**os.environ, "PYTHONUTF8": "1"}
        self.log_handle = open(LOG_FILE, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            bot_cmd,
            cwd=os.path.dirname(bot_cmd[0]) if bot_cmd[0] == BOT_EXE else REPO_DIR,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=NO_WINDOW,
        )

    def _stop_bot(self) -> None:
        # 봇이 실행 위치에 따라 다른 .env/플래그 경로를 보므로 양쪽에 신호를 남긴다
        flags = [
            os.path.join(REPO_DIR, "shutdown.flag"),
            os.path.join(REPO_DIR, "dist", "shutdown.flag"),
        ]
        running = (self.proc is not None and self.proc.poll() is None) or find_bot_pids()

        if running:
            # 1단계: 정상 종료 요청 (디스코드 로그아웃 + 음성 채널 퇴장)
            self.log("[봇] 정상 종료 요청 중... (디스코드 로그아웃)")
            for flag in flags:
                try:
                    with open(flag, "w"):
                        pass
                except OSError:
                    pass
            for _ in range(8):  # 최대 약 8초 대기
                time.sleep(1)
                own_alive = self.proc is not None and self.proc.poll() is None
                if not own_alive and not find_bot_pids():
                    self.log("[봇] 정상 종료 완료")
                    break
            else:
                self.log("[봇] 정상 종료 응답이 없어 강제 종료합니다")

        # 2단계: 아직 살아있으면 강제 종료 (fallback)
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        if self.log_handle:
            try:
                self.log_handle.close()
            except OSError:
                pass
            self.log_handle = None
        for pid in find_bot_pids():
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                creationflags=NO_WINDOW,
            )
        # 봇이 강제 종료되어 고아가 된 커스텀 TTS 서버도 정리
        for pid in find_tts_server_pids():
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                creationflags=NO_WINDOW,
            )
        # 소비되지 않은 종료 신호 정리 (봇이 이미 꺼져 있던 경우)
        for flag in flags:
            try:
                os.remove(flag)
            except OSError:
                pass

    # ---------- 상태 표시 ----------

    def _status_loop(self) -> None:
        while True:
            self._refresh_status()
            time.sleep(3)

    def _refresh_status(self) -> None:
        own = self.proc is not None and self.proc.poll() is None
        running = own or bool(find_bot_pids())
        label = "● 봇 실행 중" if running else "● 봇 중지됨"
        color = "#2e8b57" if running else "#c0392b"

        def update():
            self.status_label.configure(text=label, foreground=color)
            if not self.busy:
                self.start_btn.configure(state="disabled" if running else "normal")
                self.stop_btn.configure(state="normal" if running else "disabled")
                self.restart_btn.configure(state="normal" if running else "disabled")

        try:
            self.root.after(0, update)
        except RuntimeError:
            pass

    # ---------- 로그 표시 ----------

    def _tail_log_loop(self) -> None:
        pos = 0
        first = True
        while True:
            try:
                size = os.path.getsize(LOG_FILE)
                if size < pos:
                    pos = 0
                    self.log_queue.put("\n----- 로그 파일이 새로 시작되었습니다 -----\n")
                if size > pos or first:
                    with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
                        f.seek(pos)
                        data = f.read()
                        pos = f.tell()
                    if data:
                        self.log_queue.put(data)
            except OSError:
                pass
            first = False
            time.sleep(0.5)

    def _poll_log_queue(self) -> None:
        try:
            while True:
                data = self.log_queue.get_nowait()
                self.text.configure(state="normal")
                self.text.insert("end", data)
                lines = int(self.text.index("end-1c").split(".")[0])
                if lines > MAX_LOG_LINES:
                    self.text.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
                self.text.see("end")
                self.text.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(300, self._poll_log_queue)


def main() -> None:
    autostart = "--autostart" in sys.argv

    # 단일 인스턴스: 이미 실행 중이면 기존 창을 앞으로 가져오고 종료
    lock_sock = try_acquire_singleton()
    if lock_sock is None:
        if notify_existing_instance(autostart):
            return
        # 포트를 다른 앱이 쓰는 드문 경우 — 잠금 없이 그냥 실행

    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    BotGui(root, autostart=autostart, lock_sock=lock_sock)
    root.mainloop()
    os._exit(0)  # pystray 스레드가 남아도 확실히 종료


if __name__ == "__main__":
    main()
