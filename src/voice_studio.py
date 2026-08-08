"""내 목소리 만들기 — 녹음 스튜디오 창 (Phase 1).

PC 마이크로 스크립트 문장을 녹음해 TTS 학습용 데이터셋을 만든다.
데이터셋 구조:
    my_voice/<이름>/wavs/001.wav ...
    my_voice/<이름>/metadata.csv   (파일명|문장 형식)
"""

import os
import threading

import numpy as np
import sounddevice as sd
import soundfile as sf
import tkinter as tk
from tkinter import messagebox, ttk

SAMPLE_RATE = 48000  # 학습 시 모델에 맞춰 리샘플링하므로 원본은 고품질로 저장

# 음소·문형(평서/의문/감탄)·숫자를 고르게 담은 녹음 스크립트
SCRIPT = [
    "안녕하세요, 오늘도 좋은 하루 보내고 계신가요?",
    "이 문장은 제 목소리를 학습시키기 위한 녹음입니다.",
    "내일 아침 일곱 시 삼십 분에 알람을 맞춰 주세요.",
    "지금 밖에는 비가 내리고 있어서 우산을 챙겼어요.",
    "어제 본 영화가 정말 재미있어서 두 번이나 봤습니다.",
    "커피 한 잔과 따뜻한 빵 하나면 아침으로 충분해요.",
    "주말에는 친구들과 함께 한강에서 자전거를 탈 거예요.",
    "이번 프로젝트의 마감일은 다음 주 금요일까지입니다.",
    "와, 정말 대단하다! 어떻게 그런 생각을 했어?",
    "조용히 해 주세요. 아기가 지금 막 잠들었거든요.",
    "백화점 세일은 십이월 이십사일까지 진행됩니다.",
    "그 식당의 김치찌개는 맵지만 정말 맛있습니다.",
    "혹시 이 근처에 지하철역이 어디 있는지 아세요?",
    "오랜만에 만난 친구와 밤늦게까지 이야기를 나눴어요.",
    "운동을 꾸준히 하면 건강에 큰 도움이 됩니다.",
    "갑자기 정전이 되는 바람에 깜짝 놀랐잖아요!",
    "책상 위에 놓아둔 열쇠를 아무리 찾아도 없네요.",
    "다음 정류장에서 내리셔서 길을 건너시면 됩니다.",
    "올해 여름 휴가는 제주도로 떠나기로 결정했어요.",
    "컴퓨터가 갑자기 느려져서 재부팅을 해야 할 것 같아요.",
    "목소리 학습에는 다양한 문장을 읽는 것이 중요합니다.",
    "첫눈이 내리는 날에는 왠지 마음이 설레곤 해요.",
    "회의 자료는 미리 이메일로 보내 드렸습니다.",
    "강아지와 고양이 중에 어떤 동물을 더 좋아하세요?",
    "지난 시험보다 성적이 많이 올라서 기분이 좋아요.",
    "늦어서 정말 죄송합니다. 차가 너무 막혔어요.",
    "이 노래를 들으면 학창 시절이 떠오릅니다.",
    "창문을 열자 시원한 바람이 방 안으로 들어왔어요.",
    "천천히, 또박또박 읽어 주시면 더 좋은 결과를 얻을 수 있어요.",
    "녹음에 참여해 주셔서 감사합니다. 이제 마지막 문장입니다!",
]


def list_input_devices() -> list:
    """(장치 인덱스, 이름) 목록 — 입력 채널이 있는 장치만."""
    devices = []
    try:
        for i, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:
                devices.append((i, dev["name"]))
    except Exception:
        pass
    return devices


class VoiceStudio(tk.Toplevel):
    def __init__(self, parent: tk.Misc, repo_dir: str):
        super().__init__(parent)
        self.repo_dir = repo_dir
        self.dataset_root = os.path.join(repo_dir, "my_voice")
        self.idx = 0
        self.recording = False
        self.stream = None
        self.frames: list = []

        self.title("내 목소리 만들기 — 녹음 스튜디오")
        self.geometry("640x430")
        self.minsize(560, 400)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        pad = {"padx": 12, "pady": 4}
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)

        # 이름 + 마이크 선택
        top = ttk.Frame(frame)
        top.pack(fill="x")
        ttk.Label(top, text="목소리 이름:").pack(side="left")
        self.name_var = tk.StringVar(value="내목소리")
        name_entry = ttk.Entry(top, textvariable=self.name_var, width=16)
        name_entry.pack(side="left", padx=(4, 16))
        name_entry.bind("<FocusOut>", lambda _e: self._reload_dataset())

        ttk.Label(top, text="마이크:").pack(side="left")
        self.devices = list_input_devices()
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(
            top,
            textvariable=self.device_var,
            values=[name for _i, name in self.devices],
            state="readonly",
            width=28,
        )
        if self.devices:
            self.device_combo.current(0)
        self.device_combo.pack(side="left", padx=4)

        ttk.Label(
            frame,
            text="※ 본인 목소리 또는 녹음에 동의한 사람의 목소리만 사용해주세요.",
            foreground="#c08030",
            font=("맑은 고딕", 8),
        ).pack(anchor="w", pady=(6, 0))

        # 진행 상황 + 문장 표시
        self.progress_label = ttk.Label(frame, text="", font=("맑은 고딕", 10, "bold"))
        self.progress_label.pack(anchor="w", pady=(10, 2))

        self.sentence_text = tk.Text(
            frame, height=3, wrap="word", font=("맑은 고딕", 13),
            state="disabled", relief="flat", background="#f0f0f0",
        )
        self.sentence_text.pack(fill="x", pady=4)

        self.status_label = ttk.Label(frame, text="", foreground="#888888")
        self.status_label.pack(anchor="w", pady=2)

        # 버튼들
        btns = ttk.Frame(frame)
        btns.pack(pady=10)
        self.record_btn = ttk.Button(btns, text="● 녹음 시작", command=self.toggle_record, width=14)
        self.record_btn.grid(row=0, column=0, padx=3)
        self.play_btn = ttk.Button(btns, text="▶ 미리듣기", command=self.play_current, width=12)
        self.play_btn.grid(row=0, column=1, padx=3)
        ttk.Button(btns, text="← 이전", command=lambda: self.move(-1), width=8).grid(row=0, column=2, padx=3)
        ttk.Button(btns, text="다음 →", command=lambda: self.move(1), width=8).grid(row=0, column=3, padx=3)

        # 문장별 녹음 여부 목록
        self.listbox = tk.Listbox(frame, height=8, font=("맑은 고딕", 9))
        self.listbox.pack(fill="both", expand=True, pady=(6, 0))
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        self._reload_dataset()

    # ---------- 경로 ----------

    def _dataset_dir(self) -> str:
        name = self.name_var.get().strip() or "내목소리"
        return os.path.join(self.dataset_root, name)

    def _wav_path(self, idx: int) -> str:
        return os.path.join(self._dataset_dir(), "wavs", f"{idx + 1:03d}.wav")

    # ---------- 데이터셋 ----------

    def _reload_dataset(self) -> None:
        self._refresh_list()
        self._show_sentence()

    def _rewrite_metadata(self) -> None:
        """녹음된 파일 기준으로 metadata.csv 를 다시 쓴다 (파일명|문장)."""
        lines = []
        for i, sentence in enumerate(SCRIPT):
            if os.path.exists(self._wav_path(i)):
                lines.append(f"{i + 1:03d}.wav|{sentence}")
        path = os.path.join(self._dataset_dir(), "metadata.csv")
        os.makedirs(self._dataset_dir(), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))

    def _recorded_count(self) -> int:
        return sum(1 for i in range(len(SCRIPT)) if os.path.exists(self._wav_path(i)))

    # ---------- UI 갱신 ----------

    def _refresh_list(self) -> None:
        self.listbox.delete(0, "end")
        for i, sentence in enumerate(SCRIPT):
            mark = "✔" if os.path.exists(self._wav_path(i)) else "  "
            preview = sentence if len(sentence) <= 30 else sentence[:30] + "..."
            self.listbox.insert("end", f"{mark} {i + 1:02d}. {preview}")

    def _show_sentence(self) -> None:
        done = self._recorded_count()
        self.progress_label.config(
            text=f"문장 {self.idx + 1} / {len(SCRIPT)}  (녹음 완료: {done}개)"
        )
        self.sentence_text.config(state="normal")
        self.sentence_text.delete("1.0", "end")
        self.sentence_text.insert("1.0", SCRIPT[self.idx])
        self.sentence_text.config(state="disabled")

        recorded = os.path.exists(self._wav_path(self.idx))
        self.status_label.config(
            text="✔ 녹음됨 — 다시 녹음하면 덮어씁니다" if recorded else "아직 녹음되지 않았습니다"
        )
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(self.idx)
        self.listbox.see(self.idx)

    def _on_select(self, _event) -> None:
        if self.recording:
            return
        selection = self.listbox.curselection()
        if selection:
            self.idx = selection[0]
            self._show_sentence()

    def move(self, delta: int) -> None:
        if self.recording:
            return
        self.idx = max(0, min(len(SCRIPT) - 1, self.idx + delta))
        self._show_sentence()

    # ---------- 녹음 ----------

    def toggle_record(self) -> None:
        if self.recording:
            self._stop_record()
        else:
            self._start_record()

    def _start_record(self) -> None:
        device = None
        if self.devices and self.device_combo.current() >= 0:
            device = self.devices[self.device_combo.current()][0]
        self.frames = []
        try:
            self.stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                device=device,
                callback=lambda indata, *_: self.frames.append(indata.copy()),
            )
            self.stream.start()
        except Exception as e:
            messagebox.showerror("녹음", f"마이크를 열 수 없습니다:\n{e}", parent=self)
            return
        self.recording = True
        self.record_btn.config(text="■ 녹음 정지(저장)")
        self.status_label.config(text="🔴 녹음 중... 문장을 읽고 정지를 누르세요", foreground="#c0392b")

    def _stop_record(self) -> None:
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
        self.stream = None
        self.recording = False
        self.record_btn.config(text="● 녹음 시작")
        self.status_label.config(foreground="#888888")

        if not self.frames:
            self.status_label.config(text="녹음된 소리가 없습니다")
            return
        audio = np.concatenate(self.frames)
        if len(audio) < SAMPLE_RATE * 0.5:
            self.status_label.config(text="녹음이 너무 짧아 저장하지 않았습니다 (0.5초 미만)")
            return

        os.makedirs(os.path.dirname(self._wav_path(self.idx)), exist_ok=True)
        sf.write(self._wav_path(self.idx), audio, SAMPLE_RATE)
        self._rewrite_metadata()
        self._refresh_list()

        done = self._recorded_count()
        if done >= len(SCRIPT):
            self._show_sentence()
            messagebox.showinfo(
                "녹음 완료",
                "모든 문장의 녹음이 끝났습니다!\n"
                f"데이터셋 위치: {self._dataset_dir()}\n"
                "다음 단계(학습)는 준비되는 대로 GUI에 추가됩니다.",
                parent=self,
            )
            return
        # 다음 미녹음 문장으로 자동 이동
        for step in range(1, len(SCRIPT) + 1):
            nxt = (self.idx + step) % len(SCRIPT)
            if not os.path.exists(self._wav_path(nxt)):
                self.idx = nxt
                break
        self._show_sentence()

    # ---------- 미리듣기 ----------

    def play_current(self) -> None:
        path = self._wav_path(self.idx)
        if not os.path.exists(path):
            self.status_label.config(text="이 문장은 아직 녹음되지 않았습니다")
            return

        def _play():
            try:
                data, sr = sf.read(path, dtype="float32")
                sd.play(data, sr)
                sd.wait()
            except Exception:
                pass

        threading.Thread(target=_play, daemon=True).start()

    def _on_close(self) -> None:
        if self.recording:
            self._stop_record()
        try:
            sd.stop()
        except Exception:
            pass
        self.destroy()
