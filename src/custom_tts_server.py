"""커스텀 TTS 추론 서버 (Qwen3-TTS 제로샷 보이스 클로닝).

my_voice/<이름>/ 의 녹음 데이터를 참조 음성으로 사용해 그 목소리로 합성한다.
봇(tts.py)은 이 서버에 HTTP 로 요청만 보내므로, 무거운 torch/모델 의존성은
tts_env 가상환경에만 존재한다.

실행: bat\\start_tts_server.bat  (또는 tts_env\\Scripts\\python src\\custom_tts_server.py)

API:
    GET  /health              → {"status": "ok", "device": "cuda"}
    GET  /voices              → {"voices": ["내목소리", ...]}
    POST /synthesize          → {"text": ..., "voice": ...} 요청, WAV 바이트 응답
"""

import io
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import soundfile as sf

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MY_VOICE_DIR = os.path.join(BASE_DIR, "my_voice")
MY_VOICE_MODELS_DIR = os.path.join(BASE_DIR, "my_voice_models")
HOST = "127.0.0.1"
PORT = 51770
MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"

# VRAM 한계로 모델은 한 번에 하나만 올린다.
# loaded_kind: "base"(제로샷 클로닝용) 또는 파인튜닝된 목소리 이름
model = None
loaded_kind = None
device = "cpu"
clone_prompts: dict = {}  # 목소리 이름 -> 재사용 가능한 클로닝 프롬프트


def finetuned_model_dir(voice_name: str) -> str | None:
    path = os.path.join(MY_VOICE_MODELS_DIR, voice_name)
    if os.path.isfile(os.path.join(path, "config.json")):
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
            if finetuned_model_dir(name):
                voices.add(name)
    return sorted(voices)


def _load(model_path: str, kind: str) -> None:
    """모델을 로드한다. 이미 다른 모델이 올라가 있으면 내리고 교체한다."""
    global model, loaded_kind, device
    import torch
    from qwen_tts import Qwen3TTSModel

    if loaded_kind == kind:
        return
    if model is not None:
        print(f"[서버] '{loaded_kind}' 모델 해제 중...", flush=True)
        del model
        model = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
    print(f"[서버] 모델 로딩 중... ({model_path}, {device})", flush=True)
    model = Qwen3TTSModel.from_pretrained(
        model_path,
        device_map=device,
        dtype=dtype,
    )
    loaded_kind = kind
    clone_prompts.clear()  # 이전 모델로 만든 프롬프트는 무효
    print("[서버] 모델 로딩 완료", flush=True)


def load_model() -> None:
    _load(MODEL_ID, "base")


def get_reference(voice_name: str) -> tuple:
    """데이터셋에서 참조 음성 하나를 고른다 (가장 긴 녹음 = 안정적)."""
    dataset = os.path.join(MY_VOICE_DIR, voice_name)
    meta_path = os.path.join(dataset, "metadata.csv")
    best = None
    with open(meta_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "|" not in line:
                continue
            fname, _, text = line.partition("|")
            wav_path = os.path.join(dataset, "wavs", fname)
            if not os.path.exists(wav_path):
                continue
            size = os.path.getsize(wav_path)
            if best is None or size > best[2]:
                best = (wav_path, text, size)
    if best is None:
        raise FileNotFoundError(f"'{voice_name}' 데이터셋에 사용할 녹음이 없습니다")
    return best[0], best[1]


def get_clone_prompt(voice_name: str):
    if voice_name not in clone_prompts:
        ref_audio, ref_text = get_reference(voice_name)
        print(f"[서버] '{voice_name}' 클로닝 프롬프트 생성 중... (참조: {os.path.basename(ref_audio)})", flush=True)
        clone_prompts[voice_name] = model.create_voice_clone_prompt(
            ref_audio=ref_audio,
            ref_text=ref_text,
            x_vector_only_mode=False,
        )
    return clone_prompts[voice_name]


def synthesize(text: str, voice_name: str) -> bytes:
    ft_dir = finetuned_model_dir(voice_name)
    if ft_dir:
        # 파인튜닝된 모델이 있으면 그 모델로 합성 (필요 시 모델 교체)
        _load(ft_dir, voice_name)
        speaker = voice_name
        meta_path = os.path.join(ft_dir, "meta.json")
        if os.path.isfile(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                speaker = json.load(f).get("speaker", voice_name)
        wavs, sr = model.generate_custom_voice(text=text, speaker=speaker)
    else:
        # 녹음만 있으면 베이스 모델 제로샷 클로닝
        _load(MODEL_ID, "base")
        prompt = get_clone_prompt(voice_name)
        wavs, sr = model.generate_voice_clone(
            text=text,
            language="Korean",
            voice_clone_prompt=prompt,
        )
    buf = io.BytesIO()
    sf.write(buf, wavs[0], sr, format="WAV")
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
            audio = synthesize(text, voice)
        except Exception as e:
            print(f"[서버] 합성 실패: {type(e).__name__}: {e}", flush=True)
            self._send_json({"error": str(e)}, 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio)))
        self.end_headers()
        self.wfile.write(audio)

    def log_message(self, fmt, *args):  # 기본 액세스 로그 대신 간단히
        print(f"[서버] {self.command} {self.path}", flush=True)


def main() -> None:
    if sys.stdout:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    load_model()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[서버] http://{HOST}:{PORT} 에서 대기 중 (Ctrl+C 로 종료)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
