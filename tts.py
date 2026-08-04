"""TTS 엔진 모듈.

- Edge TTS: 무료, 기본 엔진.
- Typecast: .env 에 TYPECAST_API_KEY 가 있으면 목소리 목록에 추가된다. (유료 크레딧 소모)

교체/추가 시 available_voices() 와 synthesize() 인터페이스만 유지하면 된다.
"""

import os
import tempfile

import aiohttp
import edge_tts
from dotenv import load_dotenv

load_dotenv()

TYPECAST_URL = "https://api.typecast.ai/v1/text-to-speech"
TYPECAST_SUBSCRIPTION_URL = "https://api.typecast.ai/v1/users/me/subscription"
TYPECAST_MODEL = "ssfm-v30"

# 표시 이름 -> {engine, id}
EDGE_VOICES = {
    "선히": {"engine": "edge", "id": "ko-KR-SunHiNeural"},
    "인준": {"engine": "edge", "id": "ko-KR-InJoonNeural"},
    "현수": {"engine": "edge", "id": "ko-KR-HyunsuMultilingualNeural"},
}

# Typecast 한국어 음성 중 일부만 선별 (전체 목록: https://studio.typecast.ai/developers/api/voices)
TYPECAST_VOICES = {
    "서현": {"engine": "typecast", "id": "tc_69f2e455ea79fd197aa0476f"},
    "상현": {"engine": "typecast", "id": "tc_69fc0cff784968297fb45daa"},
    "주완": {"engine": "typecast", "id": "tc_69e0462f3e5413d26878521e"},
    "우니": {"engine": "typecast", "id": "tc_69c1f8e4f8842d80fbe7fa4f"},
    "옥지": {"engine": "typecast", "id": "tc_699d27b557c86e3f4249c051"},
    "몽실": {"engine": "typecast", "id": "tc_699d27e0e061695d6ed39bc6"},
    "찬구": {"engine": "typecast", "id": "tc_5c547544fcfee90007fed455"},
    "삐뚤어진 찬구": {"engine": "typecast", "id": "tc_6010088f885570093ad24d53"},
}

DEFAULT_VOICE = "선히"

# 재생 속도(배속) 범위 — Typecast audio_tempo 지원 범위에 맞춤
MIN_SPEED = 0.5
MAX_SPEED = 2.0
DEFAULT_SPEED = 1.0


def _typecast_key() -> str | None:
    return os.getenv("TYPECAST_API_KEY")


def available_voices() -> dict[str, dict]:
    """현재 사용 가능한 음성 목록. Typecast는 API 키가 있을 때만 포함된다."""
    voices = dict(EDGE_VOICES)
    if _typecast_key():
        voices.update(TYPECAST_VOICES)
    return voices


async def typecast_credits() -> dict | None:
    """Typecast 플랜/크레딧 정보를 반환한다. API 키가 없으면 None."""
    key = _typecast_key()
    if not key:
        return None
    async with aiohttp.ClientSession() as session:
        async with session.get(
            TYPECAST_SUBSCRIPTION_URL,
            headers={"X-API-KEY": key},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


async def synthesize(text: str, voice_name: str, speed: float = DEFAULT_SPEED) -> str:
    """텍스트를 음성으로 합성하고 생성된 오디오 파일 경로를 반환한다.

    speed 는 0.5~2.0 배속. 호출한 쪽에서 재생 후 파일을 삭제해야 한다.
    """
    speed = max(MIN_SPEED, min(MAX_SPEED, speed))
    voice = available_voices().get(voice_name) or EDGE_VOICES[DEFAULT_VOICE]
    if voice["engine"] == "typecast":
        return await _synthesize_typecast(text, voice["id"], speed)
    return await _synthesize_edge(text, voice["id"], speed)


def _make_temp(suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="discord_tts_")
    os.close(fd)
    return path


def _cleanup(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


async def _synthesize_edge(text: str, voice_id: str, speed: float) -> str:
    rate = f"{round((speed - 1) * 100):+d}%"
    path = _make_temp(".mp3")
    try:
        await edge_tts.Communicate(text, voice_id, rate=rate).save(path)
    except Exception:
        _cleanup(path)
        raise
    return path


async def _synthesize_typecast(text: str, voice_id: str, speed: float) -> str:
    payload = {
        "text": text,
        "model": TYPECAST_MODEL,
        "voice_id": voice_id,
        "output": {"audio_tempo": speed},
    }
    headers = {"X-API-KEY": _typecast_key()}
    path = _make_temp(".wav")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                TYPECAST_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                data = await resp.read()
        with open(path, "wb") as f:
            f.write(data)
    except Exception:
        _cleanup(path)
        raise
    return path
