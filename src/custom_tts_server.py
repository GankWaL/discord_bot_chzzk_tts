"""커스텀 TTS 추론 서버 (GPT-SoVITS v2).

my_voice_models/<이름>/ 의 파인튜닝 가중치(sovits.pth + gpt.ckpt)로 합성하며,
가중치가 없는 목소리는 my_voice/<이름>/ 녹음을 참조 음성으로 한 제로샷으로 동작한다.
봇(tts.py)은 이 서버에 HTTP 로 요청만 보내므로, 무거운 torch/모델 의존성은
tts_env 가상환경에만 존재한다.

사전 준비: bat\\setup_tts_server.bat (최초 1회)
실행:      bat\\start_tts_server.bat  (또는 tts_env\\Scripts\\python src\\custom_tts_server.py)

API:
    GET  /health              → {"status": "ok", "device": "cuda"}
    GET  /voices              → {"voices": ["왈", ...]}
    POST /synthesize          → {"text", "voice", "speed"} 요청, WAV 바이트 응답
"""

import io
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOVITS_REPO = os.path.join(BASE_DIR, "GPT-SoVITS")
MY_VOICE_DIR = os.path.join(BASE_DIR, "my_voice")
MY_VOICE_MODELS_DIR = os.path.join(BASE_DIR, "my_voice_models")
HOST = "127.0.0.1"
PORT = 51770

PRETRAINED = os.path.join(SOVITS_REPO, "GPT_SoVITS", "pretrained_models", "gsv-v2final-pretrained")
DEFAULT_GPT = os.path.join(PRETRAINED, "s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt")
DEFAULT_SOVITS = os.path.join(PRETRAINED, "s2G2333k.pth")

pipe = None
device = "cpu"
current_voice = None  # 현재 가중치가 로드된 목소리 이름 ("__default__" = 사전학습)
lock = threading.Lock()  # 합성은 한 번에 하나씩 (GPU 직렬화)


def finetuned_dir(voice_name: str) -> str | None:
    path = os.path.join(MY_VOICE_MODELS_DIR, voice_name)
    if os.path.isfile(os.path.join(path, "sovits.pth")) and os.path.isfile(os.path.join(path, "gpt.ckpt")):
        return path
    return None


def list_custom_voices() -> list:
    voices = set()
    if os.path.isdir(MY_VOICE_DIR):
        for name in os.listdir(MY_VOICE_DIR):
            meta = os.path.join(MY_VOICE_DIR, name, "metadata.csv")
            if os.path.isfile(meta) and os.path.getsize(meta) > 0:
                voices.add(name)
    if os.path.isdir(MY_VOICE_MODELS_DIR):
        for name in os.listdir(MY_VOICE_MODELS_DIR):
            if finetuned_dir(name):
                voices.add(name)
    return sorted(voices)


def get_reference(voice_name: str) -> tuple:
    """(참조 wav 경로, 참조 문장) — 파인튜닝 목소리는 패키지의 ref, 아니면 가장 긴 녹음."""
    ft = finetuned_dir(voice_name)
    if ft:
        ref_text = voice_name
        meta_path = os.path.join(ft, "meta.json")
        if os.path.isfile(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                ref_text = json.load(f).get("ref_text", ref_text)
        return os.path.join(ft, "ref.wav"), ref_text

    dataset = os.path.join(MY_VOICE_DIR, voice_name)
    best = None
    with open(os.path.join(dataset, "metadata.csv"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "|" not in line:
                continue
            fname, _, text = line.partition("|")
            wav = os.path.join(dataset, "wavs", fname)
            if os.path.exists(wav):
                size = os.path.getsize(wav)
                if best is None or size > best[2]:
                    best = (wav, text, size)
    if best is None:
        raise FileNotFoundError(f"'{voice_name}' 데이터셋에 사용할 녹음이 없습니다")
    return best[0], best[1]


def load_pipeline() -> None:
    global pipe, device
    import torch

    os.chdir(SOVITS_REPO)  # tts_infer.yaml 등 상대경로 기준
    sys.path.insert(0, SOVITS_REPO)
    sys.path.insert(0, os.path.join(SOVITS_REPO, "GPT_SoVITS"))
    from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[서버] GPT-SoVITS 파이프라인 로딩 중... ({device})", flush=True)
    cfg = TTS_Config("GPT_SoVITS/configs/tts_infer.yaml")
    cfg.device = device
    cfg.is_half = device == "cuda"
    cfg.t2s_weights_path = DEFAULT_GPT
    cfg.vits_weights_path = DEFAULT_SOVITS
    pipe = TTS(cfg)
    print("[서버] 파이프라인 로딩 완료", flush=True)


def switch_voice(voice_name: str) -> None:
    """요청된 목소리의 가중치로 전환한다 (수 초, 이미 로드돼 있으면 생략)."""
    global current_voice
    ft = finetuned_dir(voice_name)
    target = voice_name if ft else "__default__"
    if current_voice == target:
        return
    if ft:
        print(f"[서버] '{voice_name}' 학습 가중치 로드 중...", flush=True)
        pipe.init_t2s_weights(os.path.join(ft, "gpt.ckpt"))
        pipe.init_vits_weights(os.path.join(ft, "sovits.pth"))
    else:
        print("[서버] 사전학습 가중치(제로샷) 로드 중...", flush=True)
        pipe.init_t2s_weights(DEFAULT_GPT)
        pipe.init_vits_weights(DEFAULT_SOVITS)
    current_voice = target


def synthesize(text: str, voice_name: str, speed: float = 1.0) -> bytes:
    import numpy as np
    import soundfile as sf

    with lock:
        switch_voice(voice_name)
        ref_audio, ref_text = get_reference(voice_name)
        gen = pipe.run({
            "text": text,
            "text_lang": "ko",
            "ref_audio_path": ref_audio,
            "prompt_text": ref_text,
            "prompt_lang": "ko",
            "speed_factor": max(0.5, min(2.0, float(speed))),
        })
        sr, audio = next(gen)

    buf = io.BytesIO()
    sf.write(buf, np.asarray(audio), sr, format="WAV")
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send_json({"status": "ok", "device": device})
        elif self.path == "/voices":
            self._send_json({"voices": list_custom_voices()})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/synthesize":
            self._send_json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            text = payload["text"]
            voice = payload.get("voice") or (list_custom_voices() or [None])[0]
            if not voice:
                raise ValueError("사용 가능한 커스텀 목소리가 없습니다")
            audio = synthesize(text, voice, payload.get("speed", 1.0))
        except Exception as e:
            print(f"[서버] 합성 실패: {type(e).__name__}: {e}", flush=True)
            self._send_json({"error": str(e)}, 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio)))
        self.end_headers()
        self.wfile.write(audio)

    def log_message(self, fmt, *args):
        print(f"[서버] {self.command} {self.path}", flush=True)


def main() -> None:
    if sys.stdout:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    if not os.path.isdir(SOVITS_REPO):
        raise SystemExit("GPT-SoVITS 저장소가 없습니다. bat\\setup_tts_server.bat 을 먼저 실행해주세요.")
    load_pipeline()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[서버] http://{HOST}:{PORT} 에서 대기 중 (Ctrl+C 로 종료)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
