import os
import json
import discord
from discord.ext import commands
from discord import app_commands
from aiohttp import web
from dotenv import load_dotenv

# .env 파일에서 토큰 불러오기
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
PORT = os.getenv("PORT")

# 봇 인텐트(Intents) 설정
intents = discord.Intents.default()
intents.guilds = True

# 슬래시 전용 봇 설정
bot = commands.Bot(command_prefix="/", intents=intents)

# ----------------- 권한 관리 (내가 지정한 사람만 사용) -----------------
ALLOWED_USERS_FILE = "allowed_users.json"

def load_allowed_users() -> set[int]:
    """저장된 허용 유저 ID 목록을 불러옵니다."""
    users = set()
    # 1) 환경 변수 ALLOWED_USERS (콤마로 구분된 ID 목록)
    env_users = os.getenv("ALLOWED_USERS", "")
    for uid in env_users.split(","):
        uid = uid.strip()
        if uid.isdigit():
            users.add(int(uid))

    # 2) JSON 파일에서 불러오기
    if os.path.exists(ALLOWED_USERS_FILE):
        try:
            with open(ALLOWED_USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                users.update(data)
        except Exception as e:
            print(f"allowed_users.json 읽기 오류: {e}", flush=True)
    return users

def save_allowed_users(users: set[int]):
    """허용 유저 ID 목록을 JSON 파일에 저장합니다."""
    try:
        with open(ALLOWED_USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(users), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"allowed_users.json 저장 오류: {e}", flush=True)

allowed_user_ids = load_allowed_users()

# 봇 소유자(제작자) 확인 함수
async def is_owner(user: discord.User | discord.Member) -> bool:
    try:
        app_info = await bot.application_info()
        if app_info.team:
            return any(m.id == user.id for m in app_info.team.members)
        return user.id == app_info.owner.id
    except Exception:
        return False

# 봇 사용 허용 대상인지 확인 함수 (소유자 + 지정된 유저)
async def is_authorized(user: discord.User | discord.Member) -> bool:
    if await is_owner(user):
        return True
    return user.id in allowed_user_ids

# ----------------- 클라우드 헬스체크 웹서버 -----------------
async def start_health_check_server():
    """클라우드 호스팅(Render 등) 포트 바인딩 요구를 만족하기 위한 경량 헬스체크 웹서버"""
    if PORT:
        try:
            app = web.Application()
            app.router.add_get("/", lambda request: web.Response(text="Discord Bot is running 24/7!"))
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "0.0.0.0", int(PORT))
            await site.start()
            print(f"웹 헬스체크 서버가 포트 {PORT}에서 정상 시작되었습니다.", flush=True)
        except Exception as e:
            print(f"헬스체크 서버 시작 실패: {e}", flush=True)

# ----------------- 웹후크 헬퍼 -----------------
async def get_or_create_webhook(channel: discord.TextChannel) -> discord.Webhook:
    """해당 채널에서 봇이 사용할 수 있는 웹후크를 찾거나 새로 만듭니다."""
    try:
        webhooks = await channel.webhooks()
        for wh in webhooks:
            if wh.user == bot.user or wh.name == "ProxyChatWebhook":
                return wh
        
        if len(webhooks) < 15:
            return await channel.create_webhook(name="ProxyChatWebhook")
        else:
            return webhooks[0]
    except discord.Forbidden:
        raise PermissionError("봇에게 '웹후크 관리(Manage Webhooks)' 권한이 없습니다. 서버 설정에서 권한을 확인해주세요.")

# 공통 웹후크 전송 처리 로직
async def handle_chat(
    interaction: discord.Interaction, 
    content: str, 
    photo: discord.Attachment = None
):
    # 권한 검사 (소유자 및 지정된 유저만 허용)
    if not await is_authorized(interaction.user):
        await interaction.response.send_message(
            "⛔ 이 봇의 사용 권한이 없습니다. 봇 관리자에게 문의하세요.", 
            ephemeral=True
        )
        return

    channel = interaction.channel
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message("이 명령어는 텍스트 채널에서만 사용할 수 있습니다.", ephemeral=True)
        return

    # 유저에게만 보이는 임시 응답 (명령어 대기 처리)
    await interaction.response.defer(ephemeral=True)

    try:
        target_channel = channel.parent if isinstance(channel, discord.Thread) else channel
        webhook = await get_or_create_webhook(target_channel)

        user = interaction.user
        display_name = user.display_name
        avatar_url = user.display_avatar.url

        files = []
        if photo:
            file = await photo.to_file()
            files.append(file)

        if isinstance(channel, discord.Thread):
            await webhook.send(
                content=content,
                username=display_name,
                avatar_url=avatar_url,
                files=files,
                thread=channel
            )
        else:
            await webhook.send(
                content=content,
                username=display_name,
                avatar_url=avatar_url,
                files=files
            )

        # 전송 후 임시 응답 삭제하여 자연스러운 채팅 유지
        try:
            await interaction.delete_original_response()
        except Exception:
            await interaction.followup.send("✅ 전송되었습니다!", ephemeral=True)

    except PermissionError as pe:
        await interaction.followup.send(f"⚠️ 권한 오류: {pe}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 전송 실패: {str(e)}", ephemeral=True)


# ----------------- 봇 시작 이벤트 -----------------
@bot.event
async def on_ready():
    """봇이 켜졌을 때 중복 명령어를 정리하고 글로벌 명령어를 등록합니다."""
    print("=====================================", flush=True)
    print(f"봇 로그인 성공: {bot.user.name} (ID: {bot.user.id})", flush=True)
    print(f"현재 접속된 서버 수: {len(bot.guilds)}개", flush=True)
    
    await start_health_check_server()

    # 1) 서버별 중복 명령어 청소
    for guild in bot.guilds:
        try:
            bot.tree.clear_commands(guild=guild)
            await bot.tree.sync(guild=guild)
            print(f"[{guild.name}] 서버 전용 중복 명령어 청소 완료!", flush=True)
        except Exception as e:
            print(f"[{guild.name}] 청소 실패: {e}", flush=True)

    # 2) 단일 글로벌 슬래시 명령어 등록
    try:
        synced = await bot.tree.sync()
        print(f"글로벌 슬래시(/) 명령어 {len(synced)}개 등록 완료!", flush=True)
    except Exception as e:
        print(f"글로벌 동기화 오류: {e}", flush=True)
    print("=====================================", flush=True)
    
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.playing, 
            name="/채팅, /chat [내용] | 24시간 작동 중"
        )
    )


# ----------------- 슬래시 명령어 목록 -----------------

# 1. /채팅
@bot.tree.command(name="채팅", description="내 프로필과 닉네임으로 웹후크 메시지를 전송합니다.")
@app_commands.describe(
    내용="전송할 채팅 내용을 입력하세요.",
    사진="함께 전송할 사진 파일(선택사항)"
)
async def chat_slash_ko(
    interaction: discord.Interaction, 
    내용: str, 
    사진: discord.Attachment = None
):
    await handle_chat(interaction, 내용, 사진)


# 2. /chat
@bot.tree.command(name="chat", description="내 프로필과 닉네임으로 웹후크 메시지를 전송합니다.")
@app_commands.describe(
    message="전송할 채팅 내용을 입력하세요.",
    사진="함께 전송할 사진 파일(선택사항)"
)
async def chat_slash_en(
    interaction: discord.Interaction, 
    message: str, 
    사진: discord.Attachment = None
):
    await handle_chat(interaction, message, 사진)


# 3. /권한추가 (소유자 전용)
@bot.tree.command(name="권한추가", description="[소유자 전용] 봇을 사용할 수 있는 유저를 추가합니다.")
@app_commands.describe(유저="봇 사용을 허용할 유저를 선택하세요.")
async def add_permission(interaction: discord.Interaction, 유저: discord.User):
    if not await is_owner(interaction.user):
        await interaction.response.send_message("⛔ 봇 소유자만 이 명령어를 실행할 수 있습니다.", ephemeral=True)
        return
    
    allowed_user_ids.add(유저.id)
    save_allowed_users(allowed_user_ids)
    await interaction.response.send_message(
        f"✅ **{유저.display_name}** (`{유저.name}`) 님이 봇 사용 허용 목록에 추가되었습니다!", 
        ephemeral=True
    )


# 4. /권한제거 (소유자 전용)
@bot.tree.command(name="권한제거", description="[소유자 전용] 봇 사용 권한 목록에서 유저를 제거합니다.")
@app_commands.describe(유저="권한을 회수할 유저를 선택하세요.")
async def remove_permission(interaction: discord.Interaction, 유저: discord.User):
    if not await is_owner(interaction.user):
        await interaction.response.send_message("⛔ 봇 소유자만 이 명령어를 실행할 수 있습니다.", ephemeral=True)
        return
    
    if 유저.id in allowed_user_ids:
        allowed_user_ids.remove(유저.id)
        save_allowed_users(allowed_user_ids)
        await interaction.response.send_message(
            f"❌ **{유저.display_name}** 님이 허용 목록에서 제거되었습니다.", 
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"ℹ️ **{유저.display_name}** 님은 허용 목록에 등록되어 있지 않습니다.", 
            ephemeral=True
        )


# 5. /권한목록 (소유자 전용)
@bot.tree.command(name="권한목록", description="[소유자 전용] 현재 봇을 사용할 수 있는 유저 목록을 조회합니다.")
async def list_permission(interaction: discord.Interaction):
    if not await is_owner(interaction.user):
        await interaction.response.send_message("⛔ 봇 소유자만 이 명령어를 실행할 수 있습니다.", ephemeral=True)
        return
    
    app_info = await bot.application_info()
    owner_name = app_info.owner.name
    
    msg = f"👑 **봇 소유자**: {owner_name}\n"
    if allowed_user_ids:
        users_str = "\n".join([f"- <@{uid}> (`{uid}`)" for uid in allowed_user_ids])
        msg += f"\n👥 **사용 허용된 유저 ({len(allowed_user_ids)}명)**:\n{users_str}"
    else:
        msg += "\n👥 **추가 허용된 유저 없음** (현재 소유자 본인만 사용 가능)"
    
    await interaction.response.send_message(msg, ephemeral=True)


# 6. /핑
@bot.tree.command(name="핑", description="봇의 응답 지연 시간(Ping)을 측정합니다.")
async def ping(interaction: discord.Interaction):
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 퐁! 응답 속도: `{latency_ms}ms`", ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        print("\n[오류] .env 파일에 DISCORD_TOKEN이 설정되지 않았습니다.", flush=True)
        exit(1)
        
    print("디스코드 봇을 실행하는 중입니다...", flush=True)
    bot.run(TOKEN)
