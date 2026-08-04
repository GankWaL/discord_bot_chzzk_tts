"""TTS 엔진 모듈.

현재는 Edge TTS를 사용하지만, 나중에 CLOVA Voice 등으로 교체할 수 있도록
synthesize() 인터페이스만 유지하면 되는 구조로 분리해 둔다.
"""

import os
import tempfile

import edge_tts

# 한국어 지원 음성 목록 (표시 이름 -> Edge TTS 음성 ID)
VOICES = {
    "선히": "ko-KR-SunHiNeural",
    "인준": "ko-KR-InJoonNeural",
    "현수": "ko-KR-HyunsuMultilingualNeural",
}

DEFAULT_VOICE = "선히"


async def synthesize(text: str, voice_name: str) -> str:
    """텍스트를 음성으로 합성하고 생성된 mp3 파일 경로를 반환한다.

    호출한 쪽에서 재생 후 파일을 삭제해야 한다.
    """
    voice_id = VOICES.get(voice_name, VOICES[DEFAULT_VOICE])
    fd, path = tempfile.mkstemp(suffix=".mp3", prefix="discord_tts_")
    os.close(fd)
    try:
        await edge_tts.Communicate(text, voice_id).save(path)
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return path
