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

## 컨트롤 패널 (GUI)

`start_gui.bat` 을 더블클릭하면 봇 컨트롤 패널이 열립니다.

- **실행 / 종료 / 재시작** 버튼으로 봇 제어 (시작 프로그램으로 실행된 봇도 감지·제어)
- 봇 로그(bot.log) 실시간 확인
- 창을 닫으면 종료되지 않고 **트레이로 최소화** — 트레이 아이콘 우클릭 → 창 열기 / 봇 재시작 / 컨트롤 패널 종료
- PC 시작 시 `start_gui_hidden.vbs` (시작 프로그램 등록됨)가 GUI를 띄우면서 봇도 자동 실행

## 구조

- [src/bot.py](src/bot.py) — 봇 본체 (명령어, 세션 관리, 재생 큐)
- [src/tts.py](src/tts.py) — TTS 엔진 모듈 (Edge TTS / Typecast / Google Neural2)
- [src/bot_gui.py](src/bot_gui.py) — 컨트롤 패널 GUI
- [icon/](icon/) — 봇 아이콘 (png: 디스코드 아바타용, ico: 창/트레이용)
- `start_bot.bat` / `start_bot_hidden.vbs` — 봇만 헤드리스 실행
- `start_gui.bat` / `start_gui_hidden.vbs` — 컨트롤 패널 실행 (vbs는 봇 자동 시작 포함, 시작 프로그램용)
- `dist\` — PyInstaller 빌드 결과물 (`tts_bot.exe`, `tts_bot_gui.exe`, 커밋 제외)

## 빌드 (exe)

```powershell
python -m venv build_env
build_env\Scripts\python -m pip install -r requirements.txt pyinstaller
build_env\Scripts\python -m PyInstaller --noconfirm --onefile --icon icon\icon.ico --name tts_bot src\bot.py
build_env\Scripts\python -m PyInstaller --noconfirm --onefile --windowed --icon icon\icon.ico --name tts_bot_gui src\bot_gui.py
```

빌드 후 `dist\` 에 `.env` 와 `icon\` 폴더를 복사하면 폴더째 배포할 수 있습니다. (실행 PC에 FFmpeg 필요)
