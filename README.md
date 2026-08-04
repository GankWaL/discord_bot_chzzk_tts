# Discord TTS 봇

봇 전용 채널에 친 채팅을 TTS(Edge TTS)로 읽어 통화 채널에서 재생해주는 디스코드 봇입니다.

## 기능

- 서버 초대 시 봇 전용 텍스트 채널(`tts봇`) 자동 개설
- 통화 채널에 있는 유저가 `!시작` 을 입력하면 그 통화 채널에 봇이 입장
- 전용 채널의 채팅을 TTS로 읽어 통화 채널에서 재생
- `!목소리` 명령어로 음성 선택 가능 (선히/인준/현수)
- 봇은 시작한 유저에게 귀속 — 다른 유저의 채팅은 무시

## 사전 준비

### 1. 디스코드 봇 생성

1. [Discord 개발자 포털](https://discord.com/developers/applications)에서 **New Application** 생성
   - 봇 이름은 여기서 정합니다 (예: `TTS봇`)
2. **Bot** 탭에서:
   - **Reset Token** 으로 토큰 발급 → `.env` 에 저장
   - **Privileged Gateway Intents** 에서 **MESSAGE CONTENT INTENT** 활성화 (필수!)
3. **OAuth2 → URL Generator** 에서 초대 링크 생성:
   - Scopes: `bot`
   - Bot Permissions: `Manage Channels`, `View Channels`, `Send Messages`, `Connect`, `Speak`
   - 생성된 URL로 봇을 서버에 초대

### 2. FFmpeg 설치 (음성 재생에 필수)

```powershell
winget install Gyan.FFmpeg
```

설치 후 터미널을 재시작하고 `ffmpeg -version` 으로 확인하세요.

### 3. 파이썬 환경 설정

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 4. 토큰 설정

```powershell
copy .env.example .env
```

`.env` 파일을 열어 `DISCORD_TOKEN` 에 발급받은 토큰을 입력하세요.

## 실행

```powershell
python bot.py
```

## 사용법 (디스코드에서)

1. 통화 채널에 접속
2. `tts봇` 채널에서 `!시작` 입력 → 봇이 통화 채널에 입장
3. `tts봇` 채널에 채팅을 치면 음성으로 재생됨
4. `!종료` 로 종료

| 명령어 | 설명 |
|---|---|
| `!시작` | 내 통화 채널에 봇 입장, TTS 시작 |
| `!종료` | TTS 종료 (시작한 유저만) |
| `!목소리 <이름>` | 목소리 변경 (시작한 유저만) |
| `!목소리목록` | 사용 가능한 목소리 목록 |
| `!채널생성` | 전용 채널 재생성 |
| `!도움말` | 명령어 안내 |

## 구조

- [bot.py](bot.py) — 봇 본체 (명령어, 세션 관리, 재생 큐)
- [tts.py](tts.py) — TTS 엔진 모듈. Edge TTS 사용 중이며, 추후 CLOVA Voice 등으로 교체 시 이 파일만 수정하면 됩니다.
