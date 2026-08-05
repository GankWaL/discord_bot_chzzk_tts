"""디스코드 TTS 봇 컨트롤 패널 (Windows GUI).

- 봇 실행 / 종료 / 재시작 버튼, bot.log 실시간 확인
- 창을 닫으면 종료되지 않고 작업 표시줄 트레이로 최소화된다.
  트레이 아이콘 우클릭 → 창 열기 / 컨트롤 패널 종료
- `--autostart` 옵션으로 실행하면 GUI가 뜨면서 봇도 자동 실행된다. (시작 프로그램용)
- 시작 프로그램(vbs)으로 이미 실행된 봇도 감지해서 제어 가능

실행: start_gui.bat 더블클릭 또는 `pythonw bot_gui.py`
"""

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import pystray
from PIL import Image, ImageDraw

# 프로젝트 루트: exe(PyInstaller)면 실행 파일 위치, 아니면 src/ 의 상위 폴더
FROZEN = bool(getattr(sys, "frozen", False))
BASE_DIR = (
    os.path.dirname(sys.executable)
    if FROZEN
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
BOT_SCRIPT = os.path.join(BASE_DIR, "src", "bot.py")
BOT_EXE = os.path.join(BASE_DIR, "tts_bot.exe")
LOG_FILE = os.path.join(BASE_DIR, "bot.log")
ICON_PNG = os.path.join(BASE_DIR, "icon", "icon.png")
ICON_ICO = os.path.join(BASE_DIR, "icon", "icon.ico")
NO_WINDOW = subprocess.CREATE_NO_WINDOW
MAX_LOG_LINES = 2000


def find_bot_pids() -> list[int]:
    """bot.py 를 실행 중인 python 프로세스 PID 목록 (GUI 밖에서 시작된 것 포함)."""
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


def make_tray_image() -> Image.Image:
    if os.path.exists(ICON_PNG):
        return Image.open(ICON_PNG)
    # 아이콘 파일이 없을 때의 대체 이미지
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill="#5865F2")  # 디스코드 색
    draw.polygon([(24, 20), (24, 44), (44, 32)], fill="white")  # 재생 삼각형
    return img


class BotGui:
    def __init__(self, root: tk.Tk, autostart: bool = False):
        self.root = root
        self.proc: subprocess.Popen | None = None
        self.log_handle = None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.busy = False
        self.tray: pystray.Icon | None = None

        root.title("디스코드 TTS 봇 컨트롤 패널")
        if os.path.exists(ICON_ICO):
            try:
                root.iconbitmap(ICON_ICO)
            except tk.TclError:
                pass
        root.geometry("780x520")
        root.minsize(560, 360)

        top = ttk.Frame(root, padding=8)
        top.pack(fill="x")

        self.status_label = ttk.Label(top, text="상태 확인 중...", font=("맑은 고딕", 11, "bold"))
        self.status_label.pack(side="left")

        self.restart_btn = ttk.Button(top, text="재시작", command=lambda: self.run_action("restart"))
        self.restart_btn.pack(side="right", padx=2)
        self.stop_btn = ttk.Button(top, text="종료", command=lambda: self.run_action("stop"))
        self.stop_btn.pack(side="right", padx=2)
        self.start_btn = ttk.Button(top, text="실행", command=lambda: self.run_action("start"))
        self.start_btn.pack(side="right", padx=2)

        hint = ttk.Label(
            top,
            text="창을 닫으면 트레이로 최소화됩니다",
            foreground="#888888",
            font=("맑은 고딕", 8),
        )
        hint.pack(side="right", padx=8)

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

        self._setup_tray()
        threading.Thread(target=self._tail_log_loop, daemon=True).start()
        threading.Thread(target=self._status_loop, daemon=True).start()
        self._poll_log_queue()

        root.protocol("WM_DELETE_WINDOW", self._hide_to_tray)

        if autostart:
            self.run_action("start")

    # ---------- 트레이 ----------

    def _setup_tray(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("창 열기", self._tray_show, default=True),
            pystray.MenuItem("봇 재시작", self._tray_restart),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("컨트롤 패널 종료 (봇은 유지)", self._tray_quit),
            pystray.MenuItem("완전 종료 (봇도 종료)", self._tray_quit_all),
        )
        self.tray = pystray.Icon(
            "discord_tts_bot", make_tray_image(), "디스코드 TTS 봇", menu
        )
        self.tray.run_detached()

    def _hide_to_tray(self) -> None:
        self.root.withdraw()

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
        """버튼 액션을 작업 스레드에서 실행 (UI 멈춤 방지)."""
        if self.busy:
            return
        self.busy = True
        self._set_buttons(False)
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
            self._refresh_status()

    def _start_bot(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        if find_bot_pids():
            return  # 이미 외부에서 실행 중
        if FROZEN:
            if not os.path.exists(BOT_EXE):
                raise FileNotFoundError("tts_bot.exe 를 찾을 수 없어요. GUI와 같은 폴더에 있어야 합니다.")
            bot_cmd = [BOT_EXE]
        else:
            bot_cmd = [sys.executable, BOT_SCRIPT]
        env = {**os.environ, "PYTHONUTF8": "1"}
        self.log_handle = open(LOG_FILE, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            bot_cmd,
            cwd=BASE_DIR,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=NO_WINDOW,
        )

    def _stop_bot(self) -> None:
        # GUI가 띄운 프로세스 종료
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
        # 시작 프로그램 등 외부에서 실행된 프로세스도 종료
        for pid in find_bot_pids():
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                creationflags=NO_WINDOW,
            )

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
                self._set_buttons(True, running)

        try:
            self.root.after(0, update)
        except RuntimeError:
            pass  # 종료 중

    def _set_buttons(self, enabled: bool, running: bool | None = None) -> None:
        if not enabled:
            for b in (self.start_btn, self.stop_btn, self.restart_btn):
                b.configure(state="disabled")
            return
        if running is None:
            running = True
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")
        self.restart_btn.configure(state="normal" if running else "disabled")

    # ---------- 로그 표시 ----------

    def _tail_log_loop(self) -> None:
        pos = 0
        first = True
        while True:
            try:
                size = os.path.getsize(LOG_FILE)
                if size < pos:
                    pos = 0  # 봇 재시작으로 로그가 새로 쓰였음
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
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    BotGui(root, autostart=autostart)
    root.mainloop()
    os._exit(0)  # pystray 스레드가 남아도 확실히 종료


if __name__ == "__main__":
    main()
