# tokwen:8277700470:AAG0TUqgIZsHdb2Fc8NHUWH6Lsa4dtHNPBQ

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
from telegram.request import HTTPXRequest
import yt_dlp
import os
import asyncio
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Store user choices
user_links = {}
user_messages = {}  # Store message IDs for editing

async def start(update: Update, context: CallbackContext):
    welcome_text = (
        "👋 Welcome!\n\n"
        "Send me any video URL (YouTube, etc.) and I will help you download it.\n"
        "You can choose to download it as MP3 (audio) or MP4 (video).\n\n"
        "Just send me the link to get started!"
    )
    await update.message.reply_text(welcome_text)

# Store video quality choices
user_quality = {}

async def show_quality_options(update: Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("144p", callback_data='quality_144')],
        [InlineKeyboardButton("240p", callback_data='quality_240')],
        [InlineKeyboardButton("360p", callback_data='quality_360')],
        [InlineKeyboardButton("480p", callback_data='quality_480')],
        [InlineKeyboardButton("720p", callback_data='quality_720')],
        [InlineKeyboardButton("1080p", callback_data='quality_1080')],
        [InlineKeyboardButton("⬅️ Back", callback_data='back_to_format')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text("Choose video quality:", reply_markup=reply_markup)

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

# Store last progress update time to avoid too frequent updates
last_update_time = {}
last_percent = {}

async def safe_edit_message(context: CallbackContext, user_id: int, message_id: int, text: str):
    """Safely edit message with error handling"""
    try:
        await context.bot.edit_message_text(
            chat_id=user_id,
            message_id=message_id,
            text=text
        )
        return True
    except Exception as e:
        logger.warning(f"Failed to edit message: {e}")
        return False

def create_progress_bar(percent):
    """Create a visual progress bar"""
    filled = int(percent / 10)
    empty = 10 - filled
    return f"[{'█' * filled}{'░' * empty}] {percent}%"

async def progress_hook(d, context: CallbackContext, user_id, message_id):
    """Handle download progress updates"""
    current_time = asyncio.get_event_loop().time()
    
    if d['status'] == 'downloading':
        downloaded_bytes = d.get('downloaded_bytes', 0)
        total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate')

        if total_bytes and total_bytes > 0:
            percent = downloaded_bytes / total_bytes * 100
            percent_rounded = round(percent, 1)

            # Only update every 2 seconds and if percent changed significantly
            last_time = last_update_time.get(user_id, 0)
            if (current_time - last_time > 2.0 and 
                (last_percent.get(user_id) is None or 
                 abs(percent_rounded - last_percent[user_id]) >= 5)):
                
                last_update_time[user_id] = current_time
                last_percent[user_id] = percent_rounded
                
                progress_text = "⏬ Downloading..."
                progress_text += f"\n{create_progress_bar(percent_rounded)}"
                progress_text += f"\n💾 {downloaded_bytes/(1024*1024):.1f}MB / {total_bytes/(1024*1024):.1f}MB"
                
                await safe_edit_message(
                    context, user_id, message_id,
                    progress_text
                )
                
    elif d['status'] == 'finished':
        await safe_edit_message(
            context, user_id, message_id,
            "✅ Download finished, processing..."
        )

async def download_video(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()

    user_id = query.message.chat_id
    callback_data = query.data
    url = user_links.get(user_id)

    # Handle format selection
    if callback_data == 'mp4':
        await show_quality_options(update, context)
        return
    elif callback_data == 'back_to_format':
        keyboard = [
            [InlineKeyboardButton("🎵 MP3", callback_data='mp3')],
            [InlineKeyboardButton("🎥 MP4", callback_data='mp4')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Choose the format:", reply_markup=reply_markup)
        return
    elif callback_data.startswith('quality_'):
        quality = callback_data.split('_')[1]
        user_quality[user_id] = quality
        format_type = 'mp4'
    else:
        format_type = callback_data

    if not url:
        await query.edit_message_text("❌ URL not found. Please send it again.")
        return

    # Initial downloading message with progress bar
    progress_text = "⏬ Starting download..."
    progress_text += "\n[░░░░░░░░░░] 0%"
    message = await query.edit_message_text(progress_text)
    message_id = message.message_id
    
    # Store message ID for progress updates
    user_messages[user_id] = message_id

    # Create output directory if it doesn't exist
    output_dir = "downloads"
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, f"{user_id}_video.%(ext)s")
    
    # Progress hook that works with asyncio
    def my_hook(d):
        try:
            # Create task without waiting for it
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(progress_hook(d, context, user_id, message_id))
        except Exception as e:
            logger.error(f"Progress hook error: {e}")

    # Set format based on type and quality
    if format_type == 'mp3':
        format_spec = 'bestaudio/best'
        postprocessors = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    else:
        quality = user_quality.get(user_id, '720')
        format_spec = f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]'
        postprocessors = []

    ydl_opts = {
        'outtmpl': output_file,
        'format': format_spec,
        'postprocessors': postprocessors,
        'noplaylist': True,
        'progress_hooks': [my_hook],
        'ignoreerrors': False,
        'no_warnings': False,
        'proxy': '',
    }

    try:
        # Run yt-dlp in executor to avoid blocking
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, download_with_ytdlp, ydl_opts, url)
        
        # Update message
        await safe_edit_message(
            context, user_id, message_id,
            "📤 Uploading file..."
        )
        
        # Find and send the downloaded file
        file_sent = False
        for file in os.listdir(output_dir):
            if file.startswith(f"{user_id}_video"):
                file_path = os.path.join(output_dir, file)
                try:
                    # Check file size (Telegram limit is 50MB)
                    file_size = os.path.getsize(file_path)
                    if file_size > 50 * 1024 * 1024:  # 50MB
                        await safe_edit_message(
                            context, user_id, message_id,
                            "❌ File too large (>50MB). Try with a shorter video or different quality."
                        )
                    else:
                        with open(file_path, 'rb') as f:
                            await context.bot.send_document(
                                chat_id=user_id, 
                                document=f,
                                caption=f"✅ Downloaded successfully as {format_type.upper()}"
                            )
                        file_sent = True
                        
                        # Delete the progress message
                        try:
                            await context.bot.delete_message(chat_id=user_id, message_id=message_id)
                        except:
                            pass
                            
                except Exception as e:
                    logger.error(f"Error sending file: {e}")
                    await safe_edit_message(
                        context, user_id, message_id,
                        f"❌ Error sending file: {str(e)}"
                    )
                finally:
                    # Clean up file
                    try:
                        os.remove(file_path)
                    except:
                        pass
                break
        
        if not file_sent:
            await safe_edit_message(
                context, user_id, message_id,
                "❌ No file was downloaded. Please check the URL and try again."
            )
            
    except Exception as e:
        logger.error(f"Download error: {e}")
        await safe_edit_message(
            context, user_id, message_id,
            f"❌ Download failed: {str(e)}"
        )
    finally:
        # Cleanup
        if user_id in user_messages:
            del user_messages[user_id]
        if user_id in last_percent:
            del last_percent[user_id]
        if user_id in last_update_time:
            del last_update_time[user_id]

def download_with_ytdlp(ydl_opts, url):
    """Download video using yt-dlp (runs in executor)"""
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

# Set up the bot
def main():
    # Create custom request with increased timeout
    request = HTTPXRequest(
        connection_pool_size=8,
        read_timeout=30,
        write_timeout=30,
        connect_timeout=10,
        pool_timeout=10
    )
    
    app = (ApplicationBuilder()
           .token("8277700470:AAG0TUqgIZsHdb2Fc8NHUWH6Lsa4dtHNPBQ")
           .request(request)
           .build())

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(download_video, pattern='^(mp3|mp4|quality_|back_to_format)'))

    print("🤖 Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()