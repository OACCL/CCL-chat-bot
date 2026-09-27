import os
import discord
from discord.ext import commands
from discord import app_commands
from aiohttp import web
from dotenv import load_dotenv

# .env 파일에서 토큰 불러오기
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
PORT = os.getenv("PORT")

# 봇 인텐트(Intents) 설정 - 슬래시 명령어는 기본 인텐트만으로 작동 가능
intents = discord.Intents.default()
intents.guilds = True

# 슬래시 전용 봇 설정
bot = commands.Bot(command_prefix="/", intents=intents)


async def start_health_check_server():
    """클라우드 호스팅(Koyeb, Render 등) 포트 바인딩 요구를 만족하기 위한 경량 헬스체크 웹서버"""
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


async def get_or_create_webhook(channel: discord.TextChannel) -> discord.Webhook:
    """해당 채널에서 봇이 사용할 수 있는 웹후크를 찾거나 새로 만듭니다."""
    try:
        webhooks = await channel.webhooks()
        # 기존에 생성된 웹후크 재사용
        for wh in webhooks:
            if wh.user == bot.user or wh.name == "ProxyChatWebhook":
                return wh
        
        # 없으면 새로 생성 (채널당 최대 15개 제한 확인)
        if len(webhooks) < 15:
            return await channel.create_webhook(name="ProxyChatWebhook")
        else:
            return webhooks[0]
    except discord.Forbidden:
        raise PermissionError("봇에게 '웹후크 관리(Manage Webhooks)' 권한이 없습니다. 디스코드 서버 설정에서 봇 권한을 확인해주세요.")


# 공통 웹후크 전송 처리 로직
async def handle_chat(
    interaction: discord.Interaction, 
    content: str, 
    photo: discord.Attachment = None
):
    channel = interaction.channel
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message("이 명령어는 텍스트 채널에서만 사용할 수 있습니다.", ephemeral=True)
        return

    # 유저에게만 보이는 임시 응답 (명령어 대기 처리)
    await interaction.response.defer(ephemeral=True)

    try:
        # 스레드 여부 확인
        target_channel = channel.parent if isinstance(channel, discord.Thread) else channel
        webhook = await get_or_create_webhook(target_channel)

        # 명령어를 친 유저의 서버 닉네임과 아바타 URL 가져오기
        user = interaction.user
        display_name = user.display_name
        avatar_url = user.display_avatar.url

        # 파일 첨부 처리
        files = []
        if photo:
            file = await photo.to_file()
            files.append(file)

        # 웹후크로 해당 유저인 것처럼 메시지 전송
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

        # 실행자 화면의 임시 알림 삭제 (깔끔하게 흔적 제거)
        try:
            await interaction.delete_original_response()
        except Exception:
            await interaction.followup.send("✅ 전송되었습니다!", ephemeral=True)

    except PermissionError as pe:
        await interaction.followup.send(f"⚠️ 권한 오류: {pe}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 전송 실패: {str(e)}", ephemeral=True)


@bot.event
async def on_ready():
    """봇이 실행되었을 때 중복 명령어를 정리하고 글로벌 슬래시 명령어를 등록합니다."""
    print("=====================================", flush=True)
    print(f"봇 로그인 성공: {bot.user.name} (ID: {bot.user.id})", flush=True)
    print(f"현재 접속된 서버 수: {len(bot.guilds)}개", flush=True)
    
    # 헬스체크 웹서버 구동 (PORT 설정 시)
    await start_health_check_server()

    # 1) 서버별 중복 명령어(길드 전용)를 깨끗하게 청소 (중복 2개 뜨는 버그 해결)
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
        print(f"글로벌 슬래시(/) 명령어 {len(synced)}개 등록 완료! (/채팅, /chat, /핑)", flush=True)
    except Exception as e:
        print(f"글로벌 동기화 오류: {e}", flush=True)
    print("=====================================", flush=True)
    
    # 활동 상태 표시
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.playing, 
            name="/채팅, /chat [내용] | 24시간 작동 중"
        )
    )


# 1. 한글 슬래시 명령어: /채팅 [내용] [사진(선택)]
@bot.tree.command(
    name="채팅", 
    description="내 프로필과 닉네임으로 웹후크 메시지를 전송합니다."
)
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


# 2. 영문 슬래시 명령어: /chat [message] [사진(선택)]
@bot.tree.command(
    name="chat", 
    description="내 프로필과 닉네임으로 웹후크 메시지를 전송합니다."
)
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


# 3. 지연시간 확인: /핑
@bot.tree.command(name="핑", description="봇의 응답 지연 시간(Ping)을 측정합니다.")
async def ping(interaction: discord.Interaction):
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 퐁! 응답 속도: `{latency_ms}ms`", ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        print("\n[오류] .env 파일에 DISCORD_TOKEN이 설정되지 않았습니다.", flush=True)
        print(".env 파일을 열고 발급받은 디스코드 봇 토큰을 입력해주세요.", flush=True)
        exit(1)
        
    print("디스코드 봇을 실행하는 중입니다...", flush=True)
    bot.run(TOKEN)
