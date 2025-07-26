from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
from telegram.request import HTTPXRequest
import yt_dlp
import os
import asyncio
import logging
import time
import subprocess
import requests
import random
import shutil

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

# User agents to rotate
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0'
]

def check_ffmpeg():
    """Check if FFmpeg is available"""
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        logger.info("✅ FFmpeg is available")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.warning("⚠️ FFmpeg not found")
        return False

def check_ffprobe():
    """Check if FFprobe is available"""
    try:
        subprocess.run(['ffprobe', '-version'], capture_output=True, check=True)
        logger.info("✅ FFprobe is available")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.warning("⚠️ FFprobe not found")
        return False

# Check FFmpeg availability at startup
FFMPEG_AVAILABLE = check_ffmpeg()
FFPROBE_AVAILABLE = check_ffprobe()

async def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    group_username = "@LotusDevCommunity"

    try:
        member = await context.bot.get_chat_member(group_username, user_id)
        if member.status in ["member", "administrator", "creator"]:
            welcome_text = (
                "👋 Welcome!\n"
                "Send me any video URL (YouTube, etc.) and I will help you download it.\n"
                "You can choose to download it as MP3 (audio) or MP4 (video).\n"
                "Just send me the link to get started!"
            )
            await context.bot.send_message(chat_id=chat_id, text=welcome_text)
        else:
            raise Exception("Not a member")
    except:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Join Group", url=f"https://t.me/{group_username.strip('@')}")]])
        await context.bot.send_message(
            chat_id=chat_id,
            text="🚫 You must join our group to use this bot. Click below to join and then send /start again.",
            reply_markup=keyboard
        )

async def handle_message(update: Update, context: CallbackContext):
    url = update.message.text
    user_id = update.message.chat_id

    user_links[user_id] = url

    keyboard = [
        [InlineKeyboardButton("🎵 MP3", callback_data='mp3')],
        [InlineKeyboardButton("🎥 MP4", callback_data='mp4')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Add warning if FFmpeg is not available
    warning_text = ""
    if not FFMPEG_AVAILABLE:
        warning_text = "\n⚠️ Note: MP3 conversion may not be available without FFmpeg"
    
    await update.message.reply_text(f"Choose the format:{warning_text}", reply_markup=reply_markup)

def download_cookies():
    token = os.getenv("GITLAB_TOKEN")
    if not token:
        logger.warning("GITLAB_TOKEN not found, skipping cookie download")
        return False
        
    headers = {"PRIVATE-TOKEN": token}
    project_path = "Lotus-Kattini/telegram"
    file_path = "cookies.txt"
    branch = "main"
    import urllib.parse
    project_path_enc = urllib.parse.quote_plus(project_path)
    file_path_enc = urllib.parse.quote_plus(file_path)

    url = f"https://gitlab.com/api/v4/projects/{project_path_enc}/repository/files/{file_path_enc}/raw?ref={branch}"

    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            with open("cookies.txt", "wb") as f:
                f.write(r.content)
            logger.info("✅ cookies.txt downloaded successfully.")
            return True
        else:
            logger.error(f"❌ Failed to download cookies.txt: {r.status_code}")
            return False
    except Exception as e:
        logger.error(f"❌ Error downloading cookies: {e}")
        return False

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
        if "Message is not modified" not in str(e):
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

async def get_available_formats(url, max_retries=3):
    """Get available formats for a video with multiple client attempts"""
    clients_to_try = ['android', 'web', 'ios', 'tv_embedded']
    
    for client in clients_to_try:
        for attempt in range(max_retries):
            try:
                opts = {
                    'quiet': True,
                    'listformats': True,
                    'user_agent': random.choice(USER_AGENTS),
                    'socket_timeout': 30,
                    'extractor_args': {
                        'youtube': {
                            'player_client': [client],
                            'skip': ['hls'] if client != 'android' else [],
                        }
                    }
                }
                
                if os.path.exists('cookies.txt'):
                    opts['cookiefile'] = 'cookies.txt'
                
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    formats = info.get('formats', [])
                    
                    # Filter out image-only formats
                    video_formats = [f for f in formats if f.get('vcodec') != 'none' or f.get('acodec') != 'none']
                    audio_formats = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
                    
                    if video_formats or audio_formats:
                        logger.info(f"✅ Found formats using {client} client: {len(video_formats)} video, {len(audio_formats)} audio")
                        return info, video_formats, audio_formats, client
                        
            except Exception as e:
                logger.warning(f"Client {client} attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(random.uniform(2, 4))
                continue
    
    raise Exception("No video/audio formats available from any client")

def get_best_format_string(format_type, quality, available_formats, audio_formats):
    """Generate the best format string based on available formats"""
    
    if format_type == 'mp3':
        # For MP3, prioritize audio quality
        if audio_formats:
            return 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best[height<=720]/worst'
        else:
            return 'best[height<=720]/worst'  # Fallback to video with audio
    
    else:  # MP4
        if quality == 'best':
            return 'best[ext=mp4]/best[ext=webm]/best/worst'
        else:
            quality_num = quality.replace('p', '')
            # Create multiple fallback options
            fallbacks = [
                f'best[height<={quality_num}][ext=mp4]',
                f'best[height<={quality_num}][ext=webm]',
                f'best[height<={quality_num}]',
                f'worst[height>={int(quality_num)//2}]',  # At least half the requested quality
                'best[height<=720]/best/worst'  # Final fallback
            ]
            return '/'.join(fallbacks)

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
    
    # Check if user wants MP3 but FFmpeg is not available
    if format_type == 'mp3' and not FFMPEG_AVAILABLE:
        await query.edit_message_text(
            "⚠️ **MP3 conversion not available**\n\n"
            "FFmpeg is not installed on this server. I can download the audio in its original format (usually M4A or WebM).\n\n"
            "Would you like to continue with the original audio format?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Yes, download original audio", callback_data='audio_original')],
                [InlineKeyboardButton("❌ Cancel", callback_data='cancel')]
            ])
        )
        return
    
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

def get_enhanced_ydl_opts(output_file, format_spec, postprocessors, my_hook):
    """Get yt-dlp options with enhanced anti-detection measures"""
    
    # Download cookies if available
    cookies_available = download_cookies()
    
    # Base options
    ydl_opts = {
        'outtmpl': output_file,
        'format': format_spec,
        'postprocessors': postprocessors,
        'noplaylist': True,
        'progress_hooks': [my_hook],
        
        # Anti-detection measures
        'user_agent': random.choice(USER_AGENTS),
        'referer': 'https://www.youtube.com/',
        'sleep_interval': random.uniform(1, 3),
        'max_sleep_interval': 5,
        
        # Network settings
        'socket_timeout': 30,
        'retries': 5,
        'fragment_retries': 5,
        'skip_unavailable_fragments': True,
        
        # Headers to mimic browser behavior
        'http_headers': {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
        },
        
        # Enhanced extractor options to handle format issues
        'extractor_args': {
            'youtube': {
                'skip': ['hls'],  # Only skip HLS, keep DASH
                'player_client': ['android', 'web'],  # Use multiple clients
                'player_skip': ['configs'],  # Skip only configs, not js
                'innertube_host': 'youtubei.googleapis.com',
                'innertube_key': None,  # Let yt-dlp handle this
            }
        },
        
        # Force IPv4 to avoid potential IPv6 issues
        'force_ipv4': True,
        
        # Additional options for better format extraction
        'youtube_include_dash_manifest': True,
        'extract_flat': False,
        'ignoreerrors': False,
    }
    
    # Add FFmpeg location if available
    if FFMPEG_AVAILABLE:
        ffmpeg_path = shutil.which('ffmpeg')
        if ffmpeg_path:
            ydl_opts['ffmpeg_location'] = os.path.dirname(ffmpeg_path)
    
    # Add cookies if available
    if cookies_available and os.path.exists('cookies.txt'):
        ydl_opts['cookiefile'] = 'cookies.txt'
    
    # Add proxy if available
    proxy = os.getenv('PROXY_URL')
    if proxy:
        ydl_opts['proxy'] = proxy
    
    return ydl_opts

async def download_video(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    user_id = query.message.chat_id
    quality = query.data
    format_type = user_formats.get(user_id)
    url = user_links.get(user_id)
    
    # Handle special cases
    if quality == 'audio_original':
        format_type = 'audio'
        quality = 'best'
    elif quality == 'cancel':
        await query.edit_message_text("❌ Download cancelled.")
        return
    
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
    
    timestamp = int(time.time())
    output_file = os.path.join(output_dir, f"{user_id}_{timestamp}.%(ext)s")

    main_loop = asyncio.get_running_loop()
    def my_hook(d):
        main_loop.call_soon_threadsafe(main_loop.create_task, progress_hook(d, context, user_id, message_id))

    # Set format specification and postprocessors based on format type and FFmpeg availability
    if format_type == 'mp3' and FFMPEG_AVAILABLE:
        format_spec = 'bestaudio/best'
        postprocessors = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
    elif format_type == 'mp3' or format_type == 'audio':
        format_spec = 'bestaudio[ext=m4a]/bestaudio/best'
        postprocessors = []  # No conversion without FFmpeg
    else:  # MP4
        format_spec = 'best' if quality == 'best' else f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]'
        postprocessors = []

    # Get enhanced options
    ydl_opts = get_enhanced_ydl_opts(output_file, format_spec, postprocessors, my_hook)

    try:
        # Get available formats first
        await safe_edit_message(context, user_id, message_id, "🔍 **Checking available formats...**")
        info, video_formats, audio_formats, successful_client = await get_available_formats(url)
        
        # Get video title
        video_title = info.get('title', f'video_{user_id}').replace('/', '_').replace('\\', '_')
        video_title = ''.join(c for c in video_title if c.isalnum() or c in (' ', '-', '_', '.')).strip()
        
        # Generate optimal format string
        if format_type in ['mp3', 'audio']:
            format_spec = get_best_format_string('mp3', quality, video_formats, audio_formats)
        else:
            format_spec = get_best_format_string(format_type, quality, video_formats, audio_formats)
        
        download_type = format_type.upper() if format_type != 'audio' else 'AUDIO'
        await safe_edit_message(context, user_id, message_id,
            f"⏬ **Starting download...**\n\n📹 **Title:** {video_title}\n🎯 **Format:** {download_type}\n📺 **Quality:** {quality if format_type=='mp4' else 'N/A'}\n🔧 **Client:** {successful_client}")
        
        # Update yt-dlp options with successful client
        ydl_opts = get_enhanced_ydl_opts(output_file, format_spec, postprocessors, my_hook)
        ydl_opts['extractor_args']['youtube']['player_client'] = [successful_client]
        
        # Add random delay before download
        await asyncio.sleep(random.uniform(1, 3))
        
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
                    await asyncio.wait_for(context.bot.send_message(chat_id=user_id, text=f"✅ Downloaded! Format: {download_type}, Quality: {quality if format_type=='mp4' else 'N/A'}, Size: {file_size/(1024*1024):.1f}MB", parse_mode='Markdown'), timeout=60)
                    try:
                        await context.bot.delete_message(chat_id=user_id, message_id=message_id)
                    except:
                        pass
                os.remove(file_path)
                break
                
    except Exception as e:
        logger.error(f"Download error: {e}")
        error_msg = str(e).lower()
        
        if "ffmpeg" in error_msg or "ffprobe" in error_msg:
            await safe_edit_message(context, user_id, message_id, 
                "❌ **Audio conversion failed**\n\n"
                "FFmpeg is not available for audio processing. Try downloading as MP4 instead or contact the administrator.")
        elif "no video/audio formats available" in error_msg:
            await safe_edit_message(context, user_id, message_id, 
                "❌ **No downloadable formats found**\n\n"
                "This video might be:\n"
                "• Region restricted\n"
                "• Age restricted\n"
                "• Live stream\n"
                "• Private video\n\n"
                "Try a different video URL.")
        elif "sign in to confirm" in error_msg or "bot" in error_msg:
            await safe_edit_message(context, user_id, message_id, 
                "❌ **YouTube blocked the request**\n\n"
                "This happens due to bot detection. Please:\n"
                "• Try again in a few minutes\n"
                "• Use a different video URL\n"
                "• The issue is temporary and should resolve soon")
        elif "requested format is not available" in error_msg:
            await safe_edit_message(context, user_id, message_id,
                "❌ **Format not available**\n\n"
                "Try selecting a different quality option.\n"
                "The video might not have the requested quality available.")
        else:
            await safe_edit_message(context, user_id, message_id, f"❌ **Download failed:** {str(e)[:100]}...")
            
    finally:
        user_messages.pop(user_id, None)
        last_percent.pop(user_id, None)
        last_update_time.pop(user_id, None)
        user_formats.pop(user_id, None)

def download_with_ytdlp(ydl_opts, url):
    """Download with retry mechanism"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Add random delay between attempts
            if attempt > 0:
                time.sleep(random.uniform(5, 10))
                # Change user agent for retry
                ydl_opts['user_agent'] = random.choice(USER_AGENTS)
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            return  # Success, exit function
            
        except Exception as e:
            logger.error(f"Download attempt {attempt + 1} failed: {e}")
            if attempt == max_retries - 1:  # Last attempt
                raise e  # Re-raise the exception

# Add handler for the new callback queries
async def handle_special_callbacks(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'audio_original':
        # Set format to audio and proceed with download
        user_id = query.message.chat_id
        user_formats[user_id] = 'audio'
        # Simulate quality selection
        query.data = 'best'
        await download_video(update, context)
    elif query.data == 'cancel':
        await query.edit_message_text("❌ Download cancelled.")

def main():
    TOKEN = os.getenv("BOT_TOKEN")
    if not TOKEN:
        raise ValueError("❌ BOT_TOKEN environment variable is not set!")

    # Log FFmpeg availability
    logger.info(f"FFmpeg available: {FFMPEG_AVAILABLE}")
    logger.info(f"FFprobe available: {FFPROBE_AVAILABLE}")

    request = HTTPXRequest(connection_pool_size=8, read_timeout=30, write_timeout=30, connect_timeout=10, pool_timeout=10)
    
    app = ApplicationBuilder().token(TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(choose_quality, pattern='^(mp3|mp4)$'))
    app.add_handler(CallbackQueryHandler(download_video, pattern='^(144|240|360|480|720|1080|best)$'))
    app.add_handler(CallbackQueryHandler(handle_special_callbacks, pattern='^(audio_original|cancel)$'))
    
    logger.info("🤖 Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()