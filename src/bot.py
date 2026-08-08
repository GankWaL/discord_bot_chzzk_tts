"""디스코드 TTS 봇.

- 서버 입장 시 봇 전용 텍스트 채널을 자동 개설한다.
- 통화 채널에 있는 유저가 전용 채널에서 !시작 을 입력하면 그 통화 채널에 입장한다.
- 이후 전용 채널에서 봇을 시작한 유저(owner)가 친 채팅만 TTS로 읽어준다.
- !목소리 명령어로 음성을 변경할 수 있다.
"""

import asyncio
import json
import os
import random
import shutil
import socket
import subprocess
import sys

import discord
from discord.ext import commands
from dotenv import load_dotenv

# exe(PyInstaller) 빌드에 음성용 PyNaCl이 확실히 포함되도록 명시 import
import nacl.secret  # noqa: F401
import nacl.utils  # noqa: F401

import tts

# 로그 한글 깨짐 방지 — exe/리다이렉트 환경에서 cp949로 출력되는 문제
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None:
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

# 프로젝트 루트: exe(PyInstaller)면 실행 파일 위치, 아니면 src/ 의 상위 폴더
BASE_DIR = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

load_dotenv(os.path.join(BASE_DIR, ".env"))

# GUI가 이 파일을 만들면 봇이 정상 로그아웃 후 스스로 종료한다
SHUTDOWN_FLAG = os.path.join(BASE_DIR, "shutdown.flag")

# 유저별 마지막 목소리/속도/감정 설정 저장 파일
SETTINGS_FILE = os.path.join(BASE_DIR, "user_settings.json")

# 커스텀 TTS 서버 — 봇이 켜질 때 함께 띄우고 꺼질 때 함께 내린다
TTS_ROOT = os.path.dirname(tts.MY_VOICE_DIR)
TTS_ENV_PY = os.path.join(TTS_ROOT, "tts_env", "Scripts", "python.exe")
TTS_SERVER_SCRIPT = os.path.join(TTS_ROOT, "src", "custom_tts_server.py")
TTS_SERVER_LOG = os.path.join(TTS_ROOT, "tts_server.log")
_tts_server_proc: subprocess.Popen | None = None


def _tts_server_port_open() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 51770), timeout=1):
            return True
    except OSError:
        return False


def _tts_server_process_exists() -> bool:
    """로딩 중이라 포트가 아직 안 열린 서버까지 감지한다 (GUI 자동 시작과의 중복 방지)."""
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
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return any(line.strip().isdigit() for line in out.split())


def start_custom_tts_server() -> None:
    """커스텀 목소리와 추론 환경이 준비돼 있으면 서버를 자식 프로세스로 시작한다."""
    global _tts_server_proc
    if not os.path.exists(TTS_ENV_PY) or not os.path.exists(TTS_SERVER_SCRIPT):
        return  # 추론 환경 미구성 — 커스텀 목소리 없이 동작
    if not tts.custom_voices():
        return  # 커스텀 목소리가 없으면 띄울 필요 없음
    if _tts_server_port_open() or _tts_server_process_exists():
        print("커스텀 TTS 서버: 이미 실행 중인 서버를 사용합니다", flush=True)
        return
    log = open(TTS_SERVER_LOG, "w", encoding="utf-8")
    _tts_server_proc = subprocess.Popen(
        [TTS_ENV_PY, TTS_SERVER_SCRIPT],
        cwd=TTS_ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    print("커스텀 TTS 서버를 함께 시작했습니다 (모델 로딩까지 수십 초)", flush=True)


def stop_custom_tts_server() -> None:
    global _tts_server_proc
    if _tts_server_proc and _tts_server_proc.poll() is None:
        _tts_server_proc.terminate()
        try:
            _tts_server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _tts_server_proc.kill()
        print("커스텀 TTS 서버를 종료했습니다", flush=True)
    _tts_server_proc = None


def load_all_settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_user_setting(user_id: int, **kwargs) -> None:
    """유저의 설정 일부를 갱신해 저장한다. (voice / speed / emotion)"""
    data = load_all_settings()
    entry = data.get(str(user_id), {})
    entry.update(kwargs)
    data[str(user_id)] = entry
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"[설정 저장 실패] {e}", flush=True)

TOKEN = os.getenv("DISCORD_TOKEN")

# PATH에 ffmpeg가 없어도 winget 설치 경로에서 찾아 쓴다
FFMPEG_PATH = (
    os.getenv("FFMPEG_PATH")
    or shutil.which("ffmpeg")
    or os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe")
)

COMMAND_PREFIX = "!"
BOT_CHANNEL_NAME = "tts봇"
MAX_TTS_LENGTH = 300  # 이보다 긴 채팅은 읽지 않는다

intents = discord.Intents.default()
intents.message_content = True  # 개발자 포털에서 MESSAGE CONTENT INTENT 활성화 필요

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)


class Session:
    """길드별 TTS 세션. 봇을 시작한 유저(owner)에게 귀속된다."""

    def __init__(self, owner_id: int, voice_client: discord.VoiceClient):
        self.owner_id = owner_id
        self.voice_client = voice_client

        # 마지막으로 사용하던 설정 복원 (없거나 유효하지 않으면 기본값)
        saved = load_all_settings().get(str(owner_id), {})
        voices = tts.available_voices()
        self.voice = saved.get("voice") if saved.get("voice") in voices else tts.DEFAULT_VOICE
        speed = saved.get("speed", tts.DEFAULT_SPEED)
        self.speed = (
            float(speed)
            if isinstance(speed, (int, float)) and tts.MIN_SPEED <= speed <= tts.MAX_SPEED
            else tts.DEFAULT_SPEED
        )
        emotion = saved.get("emotion")
        self.emotion = emotion if emotion in tts.TYPECAST_EMOTIONS else tts.DEFAULT_EMOTION
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.player_task: asyncio.Task | None = None


sessions: dict[int, Session] = {}


def is_my_name(guild: discord.Guild, name: str) -> bool:
    """지정한 이름이 이 봇(서버 별명 또는 계정명)을 가리키는지 확인한다."""
    candidates = {guild.me.display_name.lower(), bot.user.name.lower()}
    return name.strip().lower() in candidates


def find_bot_channel(guild: discord.Guild) -> discord.TextChannel | None:
    return discord.utils.get(guild.text_channels, name=BOT_CHANNEL_NAME)


async def ensure_bot_channel(guild: discord.Guild) -> discord.TextChannel | None:
    channel = find_bot_channel(guild)
    if channel is None:
        try:
            channel = await guild.create_text_channel(
                BOT_CHANNEL_NAME,
                topic="TTS 봇 전용 채널입니다. !도움말 을 입력해 사용법을 확인하세요.",
            )
        except discord.Forbidden:
            return None
    return channel


async def player_loop(session: Session) -> None:
    """큐에 쌓인 텍스트를 순서대로 합성해 재생한다."""
    while True:
        text = await session.queue.get()
        try:
            path = await tts.synthesize(
                text, session.voice, session.speed, session.emotion
            )
        except Exception as e:
            print(f"[TTS 합성 오류] {type(e).__name__}: {e}", flush=True)
            continue

        finished = asyncio.Event()
        loop = asyncio.get_running_loop()
        try:
            session.voice_client.play(
                discord.FFmpegPCMAudio(path, executable=FFMPEG_PATH),
                after=lambda _err: loop.call_soon_threadsafe(finished.set),
            )
            await finished.wait()
        except Exception as e:
            print(f"[재생 오류] {type(e).__name__}: {e}", flush=True)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


async def end_session(guild_id: int) -> None:
    session = sessions.pop(guild_id, None)
    if session is None:
        return
    if session.player_task:
        session.player_task.cancel()
    if session.voice_client.is_connected():
        await session.voice_client.disconnect()


@bot.check
async def only_owner_when_bound(ctx: commands.Context):
    """세션이 잡힌 동안에는 봇 주인의 명령어에만 반응한다.

    여러 봇이 같은 채널에 있을 때, 다른 유저의 명령어는 무시해서
    그 유저와 연결된(또는 유휴 상태인) 봇만 응답하게 한다.
    !시작 은 예외 — 이름 지정 시 사용 중 안내를 위해 명령어 안에서 처리한다.
    """
    if ctx.guild is None:
        return False
    session = sessions.get(ctx.guild.id)
    if (
        session is not None
        and ctx.author.id != session.owner_id
        and ctx.command is not None
        and ctx.command.name != "시작"
    ):
        return False
    return True


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    # 체크 실패(다른 봇 몫의 명령어)와 없는 명령어는 조용히 무시
    if isinstance(error, (commands.CheckFailure, commands.CommandNotFound)):
        return
    print(f"[명령어 오류] {ctx.command}: {error}", flush=True)


async def shutdown_watcher():
    """종료 신호 파일이 생기면 음성 채널에서 나가고 정상 로그아웃한다."""
    while True:
        await asyncio.sleep(1)
        if not os.path.exists(SHUTDOWN_FLAG):
            continue
        try:
            os.remove(SHUTDOWN_FLAG)
        except OSError:
            pass
        print("종료 요청을 받았습니다. 로그아웃 중...", flush=True)
        for guild_id in list(sessions):
            try:
                await end_session(guild_id)
            except Exception:
                pass
        await bot.close()
        return


_watcher_started = False


@bot.event
async def on_ready():
    global _watcher_started
    if not _watcher_started:
        _watcher_started = True
        asyncio.create_task(shutdown_watcher())
    print(f"봇 로그인 완료: {bot.user}")
    print(f"음성 지원(PyNaCl): {discord.voice_client.has_nacl}", flush=True)
    try:
        if not discord.opus.is_loaded():
            discord.opus._load_default()
    except Exception as e:
        print(f"Opus 로드 실패: {e}", flush=True)
    print(f"음성 인코더(Opus): {discord.opus.is_loaded()}", flush=True)


@bot.event
async def on_guild_join(guild: discord.Guild):
    channel = await ensure_bot_channel(guild)
    if channel:
        await channel.send(
            "안녕하세요, TTS 봇입니다!\n"
            "통화 채널에 접속한 뒤 이 채널에서 `!시작` 을 입력하면 "
            "여기에 친 채팅을 음성으로 읽어드립니다. `!도움말` 로 전체 명령어를 확인하세요."
        )


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return

    await bot.process_commands(message)

    # 명령어가 아닌 일반 채팅만 TTS 대상으로 처리
    if message.content.startswith(COMMAND_PREFIX):
        return

    session = sessions.get(message.guild.id)
    if session is None:
        return
    if message.channel.name != BOT_CHANNEL_NAME:
        return
    if message.author.id != session.owner_id:
        return  # 봇을 시작한 유저의 채팅만 읽는다

    text = message.content.strip()
    if not text:
        return
    if len(text) > MAX_TTS_LENGTH:
        await message.channel.send(f"{MAX_TTS_LENGTH}자를 넘는 채팅은 읽을 수 없어요.")
        return
    await session.queue.put(text)


@bot.event
async def on_voice_state_update(member, before, after):
    if member.guild is None:
        return
    session = sessions.get(member.guild.id)
    if session is None:
        return

    bot_voice_channel = session.voice_client.channel

    # 봇이 강제로 통화 채널에서 추방된 경우 정리
    if member.id == bot.user.id and after.channel is None:
        await end_session(member.guild.id)
        return

    # 봇 주인이 통화 채널에서 나가면 봇도 함께 종료
    if member.id == session.owner_id and after.channel != bot_voice_channel:
        channel = find_bot_channel(member.guild)
        await end_session(member.guild.id)
        if channel:
            await channel.send("봇 주인이 통화 채널에서 나가서 TTS를 종료했어요.")


@bot.command(name="시작", aliases=["start"])
async def start(ctx: commands.Context, *, bot_name: str | None = None):
    if ctx.channel.name != BOT_CHANNEL_NAME:
        return

    # 마지막 인자가 "랜덤"이면 랜덤 목소리로 시작 (예: !시작 랜덤, !시작 TTS봇 랜덤)
    random_voice = False
    if bot_name:
        parts = bot_name.split()
        if parts and parts[-1] == "랜덤":
            random_voice = True
            bot_name = " ".join(parts[:-1]) or None

    # 봇 이름이 지정되면 내 이름과 일치할 때만 반응 (여러 봇 동시 운영 대비)
    if bot_name is not None and not is_my_name(ctx.guild, bot_name):
        return

    if ctx.guild.id in sessions:
        # 이름으로 콕 집어 불렀을 때만 사용 중 안내 (그 외에는 유휴 봇에게 양보)
        if bot_name is not None:
            owner = ctx.guild.get_member(sessions[ctx.guild.id].owner_id)
            owner_name = owner.display_name if owner else "다른 유저"
            await ctx.send(
                f"**{ctx.guild.me.display_name}** 은(는) 이미 **{owner_name}** 님이 사용 중이에요."
            )
        return

    if ctx.author.voice is None or ctx.author.voice.channel is None:
        await ctx.send("먼저 통화 채널에 접속한 뒤 `!시작` 을 입력해주세요.")
        return

    try:
        voice_client = await ctx.author.voice.channel.connect()
    except discord.ClientException:
        await ctx.send("통화 채널 접속에 실패했어요. 잠시 후 다시 시도해주세요.")
        return

    session = Session(ctx.author.id, voice_client)
    if random_voice:
        session.voice = random.choice(list(tts.available_voices()))
        save_user_setting(ctx.author.id, voice=session.voice)
    session.player_task = asyncio.create_task(player_loop(session))
    sessions[ctx.guild.id] = session

    # 커스텀 목소리면 첫 채팅 전에 미리 웜업 (가중치 전환 + 특징 추출)
    voice_info = tts.available_voices().get(session.voice, {})
    if voice_info.get("engine") == "custom":
        asyncio.create_task(tts.warmup_custom(session.voice))

    voice_label = f"현재 목소리: {session.voice}"
    if random_voice:
        voice_label = f"랜덤 목소리 선택: **{session.voice}**"
    await ctx.send(
        f"**{ctx.guild.me.display_name}** 봇이 **{ctx.author.display_name}** 님의 TTS를 시작했어요! "
        f"이 채널에 친 채팅을 읽어드립니다. ({voice_label})\n"
        "종료하려면 `!종료` 를 입력하세요."
    )


@bot.command(name="종료", aliases=["stop"])
async def stop(ctx: commands.Context):
    if ctx.channel.name != BOT_CHANNEL_NAME:
        return

    session = sessions.get(ctx.guild.id)
    if session is None:
        await ctx.send("실행 중인 TTS가 없어요.")
        return
    if ctx.author.id != session.owner_id:
        await ctx.send("봇을 시작한 유저만 종료할 수 있어요.")
        return

    await end_session(ctx.guild.id)
    await ctx.send("TTS를 종료했어요.")


@bot.command(name="목소리", aliases=["voice"])
async def set_voice(ctx: commands.Context, *, voice_name: str | None = None):
    if ctx.channel.name != BOT_CHANNEL_NAME:
        return

    if voice_name is None:
        await ctx.send("사용법: `!목소리 <이름>` — `!목소리목록` 으로 목록을 확인하세요.")
        return

    session = sessions.get(ctx.guild.id)
    if session is None:
        await ctx.send("먼저 `!시작` 으로 TTS를 시작해주세요.")
        return
    if ctx.author.id != session.owner_id:
        await ctx.send("봇을 시작한 유저만 목소리를 바꿀 수 있어요.")
        return
    picked_random = voice_name == "랜덤"
    if picked_random:
        voice_name = random.choice(list(tts.available_voices()))
    elif voice_name not in tts.available_voices():
        await ctx.send("없는 목소리예요. `!목소리목록` 으로 확인해주세요.")
        return

    session.voice = voice_name
    save_user_setting(ctx.author.id, voice=voice_name)
    if tts.available_voices().get(voice_name, {}).get("engine") == "custom":
        asyncio.create_task(tts.warmup_custom(voice_name))  # 첫 채팅 전 미리 준비
    prefix = "랜덤으로 " if picked_random else ""
    await ctx.send(f"{prefix}목소리를 **{voice_name}** (으)로 변경했어요.")


@bot.command(name="목소리목록", aliases=["voices"])
async def list_voices(ctx: commands.Context):
    if ctx.channel.name != BOT_CHANNEL_NAME:
        return
    engine_label = {
        "edge": "Edge TTS (무료)",
        "typecast": "Typecast (크레딧 소모)",
        "google": "Google Neural2 (월 무료 한도 내)",
        "custom": "커스텀 TTS (로컬 서버 필요)",
    }
    lines = []
    for name, voice in tts.available_voices().items():
        desc = f" ({voice['desc']})" if voice.get("desc") else ""
        lines.append(
            f"- **{name}**{desc} — {engine_label.get(voice['engine'], voice['engine'])}"
        )
    await ctx.send("사용 가능한 목소리:\n" + "\n".join(lines))


@bot.command(name="속도", aliases=["speed"])
async def set_speed(ctx: commands.Context, speed: float | None = None):
    if ctx.channel.name != BOT_CHANNEL_NAME:
        return

    session = sessions.get(ctx.guild.id)
    if session is None:
        await ctx.send("먼저 `!시작` 으로 TTS를 시작해주세요.")
        return

    if speed is None:
        await ctx.send(
            f"현재 재생 속도: **{session.speed:g}배속**\n"
            f"사용법: `!속도 <{tts.MIN_SPEED:g}~{tts.MAX_SPEED:g}>` (예: `!속도 1.5`)"
        )
        return

    if ctx.author.id != session.owner_id:
        await ctx.send("봇을 시작한 유저만 속도를 바꿀 수 있어요.")
        return
    if not (tts.MIN_SPEED <= speed <= tts.MAX_SPEED):
        await ctx.send(f"속도는 {tts.MIN_SPEED:g}~{tts.MAX_SPEED:g} 사이로 입력해주세요.")
        return

    session.speed = speed
    save_user_setting(ctx.author.id, speed=speed)
    await ctx.send(f"재생 속도를 **{speed:g}배속** 으로 변경했어요.")


@bot.command(name="감정", aliases=["emotion"])
async def set_emotion(ctx: commands.Context, emotion: str | None = None):
    if ctx.channel.name != BOT_CHANNEL_NAME:
        return

    session = sessions.get(ctx.guild.id)
    if session is None:
        await ctx.send("먼저 `!시작` 으로 TTS를 시작해주세요.")
        return

    emotion_list = " / ".join(tts.TYPECAST_EMOTIONS)
    if emotion is None:
        await ctx.send(
            f"현재 감정: **{session.emotion}**\n"
            f"사용법: `!감정 <이름>` — {emotion_list}\n"
            "-# 감정은 Typecast 목소리에만 적용됩니다."
        )
        return

    if ctx.author.id != session.owner_id:
        await ctx.send("봇을 시작한 유저만 감정을 바꿀 수 있어요.")
        return
    if emotion not in tts.TYPECAST_EMOTIONS:
        await ctx.send(f"없는 감정이에요. 사용 가능: {emotion_list}")
        return

    session.emotion = emotion
    save_user_setting(ctx.author.id, emotion=emotion)
    voice_info = tts.available_voices().get(session.voice, {})
    note = ""
    if voice_info.get("engine") != "typecast":
        note = f"\n-# 현재 목소리(**{session.voice}**)는 Typecast가 아니라서 감정이 적용되지 않아요."
    await ctx.send(f"감정을 **{emotion}** (으)로 변경했어요.{note}")


@bot.command(name="크레딧", aliases=["credits"])
async def credits(ctx: commands.Context):
    if ctx.channel.name != BOT_CHANNEL_NAME:
        return
    lines = []

    try:
        info = await tts.typecast_credits()
    except Exception:
        info = None
        lines.append("**Typecast** — 크레딧 정보를 가져오지 못했어요.")
    if info is not None:
        credit_info = info.get("credits", {})
        total = credit_info.get("plan_credits", 0)
        used = credit_info.get("used_credits", 0)
        percent = (used / total * 100) if total else 0
        lines.append(
            f"**Typecast** (플랜: {info.get('plan', '?')})\n"
            f"사용량: {used:,} / {total:,} ({percent:.1f}%) · 남은 크레딧: **{total - used:,}**"
        )

    if any(v["engine"] == "google" for v in tts.available_voices().values()):
        usage = tts.google_usage()
        used = usage["chars"]
        percent = used / tts.GOOGLE_SAFE_LIMIT * 100 if tts.GOOGLE_SAFE_LIMIT else 0
        lines.append(
            f"**Google Neural2** ({usage['month']} 무료 한도)\n"
            f"사용량: {used:,} / {tts.GOOGLE_SAFE_LIMIT:,}자 ({percent:.1f}%) · "
            f"남은 글자 수: **{tts.google_quota_left():,}**\n"
            f"-# 한도 도달 시 Edge TTS로 자동 전환됩니다."
        )

    if not lines:
        lines.append("설정된 외부 TTS API 키가 없어요. (Edge TTS는 무제한 무료)")
    await ctx.send("\n\n".join(lines))


@bot.command(name="채널생성")
async def create_channel(ctx: commands.Context):
    channel = find_bot_channel(ctx.guild)
    if channel:
        await ctx.send(f"이미 봇 전용 채널이 있어요: {channel.mention}")
        return
    channel = await ensure_bot_channel(ctx.guild)
    if channel:
        await ctx.send(f"봇 전용 채널을 만들었어요: {channel.mention}")
    else:
        await ctx.send("채널 생성 권한이 없어요. 봇에 `채널 관리` 권한을 부여해주세요.")


@bot.command(name="도움말", aliases=["help"])
async def help_command(ctx: commands.Context):
    await ctx.send(
        "**TTS 봇 명령어** (봇 전용 채널에서 사용)\n"
        "`!시작 [봇이름] [랜덤]` — 내가 접속한 통화 채널에 봇을 입장시키고 TTS 시작\n"
        "  (이름 지정 시 그 봇만 반응, `랜덤` 을 붙이면 랜덤 목소리로 시작)\n"
        "  마지막으로 쓰던 목소리·속도·감정은 자동 저장되어 다음 시작 때 복원됩니다\n"
        "`!종료` — TTS 종료 (시작한 유저만 가능)\n"
        "`!목소리 <이름|랜덤>` — 목소리 변경 (시작한 유저만 가능)\n"
        "`!목소리목록` — 사용 가능한 목소리 목록\n"
        "`!속도 <0.5~2>` — 재생 속도 변경 (시작한 유저만 가능)\n"
        "`!감정 <이름>` — Typecast 목소리 감정 변경 (시작한 유저만 가능)\n"
        "`!크레딧` — Typecast 크레딧 사용량/잔여량 확인\n"
        "`!채널생성` — 봇 전용 채널이 없을 때 다시 생성\n\n"
        "TTS가 시작되면, 시작한 유저가 이 채널에 친 채팅만 음성으로 읽습니다."
    )


def main():
    if not TOKEN:
        raise SystemExit(".env 파일에 DISCORD_TOKEN 을 설정해주세요. (.env.example 참고)")
    # 이전 실행이 남긴 종료 신호가 있으면 제거 (시작하자마자 꺼지는 것 방지)
    try:
        os.remove(SHUTDOWN_FLAG)
    except OSError:
        pass
    start_custom_tts_server()
    try:
        bot.run(TOKEN)
    finally:
        stop_custom_tts_server()
    print("봇이 정상적으로 종료되었습니다.", flush=True)


if __name__ == "__main__":
    main()
