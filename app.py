# tokwen:8277700470:AAG0TUqgIZsHdb2Fc8NHUWH6Lsa4dtHNPBQ

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
from telegram.request import HTTPXRequest
import yt_dlp
import os
import asyncio
import logging
import time

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Store user choices
user_links = {}
user_formats = {}  # Store chosen format (mp3/mp4)
user_messages = {}  # Store message IDs for editing

# Store last progress update time to avoid too frequent updates
last_update_time = {}
last_percent = {}

async def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    group_username = "@lotusdev_conmmunity"

    try:
        member = await context.bot.get_chat_member(group_username, user_id)
        if member.status in ["member", "administrator", "creator"]:
            welcome_text = (
                "👋 Welcome!"
                "Send me any video URL (YouTube, etc.) and I will help you download it."
                "You can choose to download it as MP3 (audio) or MP4 (video)."
                "Just send me the link to get started!"
            )
            await context.bot.send_message(chat_id=chat_id, text=welcome_text)
        else:
            raise Exception("Not a member")
    except:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Join Group", url=f"https://t.me/{group_username.strip('@')}")]])
        await context.bot.send_message(
            chat_id=chat_id,
            text="🚫 You must join our group to use this bot.Click below to join and then send /start again.",
            reply_markup=keyboard
        )

async def handle_message(update: Update, context: CallbackContext):
    url = update.message.text
    user_id = update.message.chat_id

    # Save URL for this user
    user_links[user_id] = url

    # Ask format
    keyboard = [
        [InlineKeyboardButton("🎵 MP3", callback_data='mp3')],
        [InlineKeyboardButton("🎥 MP4", callback_data='mp4')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Choose the format:", reply_markup=reply_markup)

async def safe_edit_message(context: CallbackContext, user_id: int, message_id: int, text: str):
    try:
        await context.bot.edit_message_text(
            chat_id=user_id,
            message_id=message_id,
            text=text,
            parse_mode='Markdown'
        )
        return True
    except Exception as e:
        logger.warning(f"Failed to edit message: {e}")
        return False

async def progress_hook(d, context: CallbackContext, user_id, message_id):
    try:
        if d.get('status') == 'downloading':
            downloaded_bytes = d.get('downloaded_bytes', 0)
            total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate')
            speed = d.get('speed', 0)
            current_time = time.time()
            if total_bytes and total_bytes > 0:
                percent = downloaded_bytes / total_bytes * 100
                percent_rounded = round(percent, 1)
                downloaded_mb = downloaded_bytes / (1024 * 1024)
                total_mb = total_bytes / (1024 * 1024)
                bar = '⬢' * int(20 * percent / 100) + '⬡' * (20 - int(20 * percent / 100))
                if (current_time - last_update_time.get(user_id, 0) > 1.0 and
                    (last_percent.get(user_id) is None or abs(percent_rounded - last_percent[user_id]) >= 1)):
                    last_update_time[user_id] = current_time
                    last_percent[user_id] = percent_rounded
                    await safe_edit_message(context, user_id, message_id,
                        f"⏬ **Downloading...**\n\n{bar} **{percent_rounded}%**\n\n📊 **Progress:** `{downloaded_mb:.1f}MB / {total_mb:.1f}MB`\n⚡ **Speed:** `{speed / (1024*1024):.1f} MB/s`")
        elif d.get('status') == 'finished':
            await safe_edit_message(context, user_id, message_id, "✅ Download finished, processing...")
    except Exception as e:
        logger.error(f"Progress hook error: {e}")

async def choose_quality(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    user_id = query.message.chat_id
    format_type = query.data
    url = user_links.get(user_id)
    if not url:
        await query.edit_message_text("❌ URL not found. Please send it again.")
        return
    user_formats[user_id] = format_type
    if format_type == 'mp3':
        await download_video(update, context)
    else:
        keyboard = [
            [InlineKeyboardButton("144p", callback_data='144'), InlineKeyboardButton("240p", callback_data='240')],
            [InlineKeyboardButton("360p", callback_data='360'), InlineKeyboardButton("480p", callback_data='480')],
            [InlineKeyboardButton("720p", callback_data='720'), InlineKeyboardButton("1080p", callback_data='1080')],
            [InlineKeyboardButton("Best Available", callback_data='best')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Choose video quality:", reply_markup=reply_markup)

async def download_video(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    user_id = query.message.chat_id
    quality = query.data
    format_type = user_formats.get(user_id)
    url = user_links.get(user_id)
    if not url or not format_type:
        await query.edit_message_text("❌ Missing information. Please start over.")
        return

    message = await query.edit_message_text(f"🔍 Getting video info...")
    message_id = message.message_id
    user_messages[user_id] = message_id
    last_update_time[user_id] = 0
    last_percent[user_id] = 0

    output_dir = "downloads"
    os.makedirs(output_dir, exist_ok=True)
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'proxy': ''}) as ydl:
            info = ydl.extract_info(url, download=False)
            video_title = info.get('title', 'video').replace('/', '_').replace('\\', '_')
            video_title = ''.join(c for c in video_title if c.isalnum() or c in (' ', '-', '_', '.')).strip()
    except Exception:
        video_title = f"video_{user_id}"

    timestamp = int(time.time())
    output_file = os.path.join(output_dir, f"{user_id}_{timestamp}.%(ext)s")

    main_loop = asyncio.get_running_loop()
    def my_hook(d):
        main_loop.call_soon_threadsafe(main_loop.create_task, progress_hook(d, context, user_id, message_id))

    format_spec = 'bestaudio/best' if format_type == 'mp3' else ('best' if quality == 'best' else f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]')
    postprocessors = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}] if format_type == 'mp3' else []

    ydl_opts = {
        'outtmpl': output_file,
        'format': format_spec,
        'postprocessors': postprocessors,
        'noplaylist': True,
        'progress_hooks': [my_hook],
        'proxy': ''
    }

    try:
        await safe_edit_message(context, user_id, message_id,
            f"⏬ **Starting download...**\n\n📹 **Title:** {video_title}\n🎯 **Format:** {format_type.upper()}\n📺 **Quality:** {quality if format_type=='mp4' else 'N/A'}")
        await main_loop.run_in_executor(None, download_with_ytdlp, ydl_opts, url)
        await safe_edit_message(context, user_id, message_id, "📤 **Uploading file...**")

        for file in os.listdir(output_dir):
            if file.startswith(f"{user_id}_{timestamp}"):
                file_path = os.path.join(output_dir, file)
                file_size = os.path.getsize(file_path)
                if file_size > 50*1024*1024:
                    await safe_edit_message(context, user_id, message_id, "❌ File too large (>50MB). Try different quality.")
                else:
                    with open(file_path, 'rb') as f:
                        await asyncio.wait_for(context.bot.send_document(chat_id=user_id, document=f, filename=video_title + os.path.splitext(file)[1]), timeout=60)
                    await asyncio.wait_for(context.bot.send_message(chat_id=user_id, text=f"✅ Downloaded! Format: {format_type.upper()}, Quality: {quality if format_type=='mp4' else 'N/A'}, Size: {file_size/(1024*1024):.1f}MB", parse_mode='Markdown'), timeout=60)
                    try:
                        await context.bot.delete_message(chat_id=user_id, message_id=message_id)
                    except:
                        pass
                os.remove(file_path)
                break
    except Exception as e:
        logger.error(f"Download error: {e}")
        await safe_edit_message(context, user_id, message_id, f"⚠️ *Upload timeout:* File sent successfully, but status update failed due to slow connection.")
    finally:
        user_messages.pop(user_id, None)
        last_percent.pop(user_id, None)
        last_update_time.pop(user_id, None)
        user_formats.pop(user_id, None)


def download_with_ytdlp(ydl_opts, url):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

def main():
    request = HTTPXRequest(connection_pool_size=8, read_timeout=30, write_timeout=30, connect_timeout=10, pool_timeout=10)
    app = (ApplicationBuilder().token("8277700470:AAG0TUqgIZsHdb2Fc8NHUWH6Lsa4dtHNPBQ").request(request).build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(choose_quality, pattern='^(mp3|mp4)$'))
    app.add_handler(CallbackQueryHandler(download_video, pattern='^(144|240|360|480|720|1080|best)$'))
    print("🤖 Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
