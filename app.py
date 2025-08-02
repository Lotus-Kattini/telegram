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
import socket

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

def check_network():
    """Check basic network connectivity"""
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=5)
        logger.info("✅ Network connectivity OK")
        return True
    except OSError:
        logger.warning("⚠️ Network connectivity issues detected")
        return False

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

async def get_available_formats(url, max_retries=2):
    """Get available formats for a video with robust error handling"""
    
    # Check network connectivity first
    if not check_network():
        raise Exception("Network connectivity issues detected")
    
    # Try different strategies
    strategies = [
        {
            'name': 'web_basic',
            'opts': {
                'quiet': True,
                'listformats': True,
                'user_agent': random.choice(USER_AGENTS),
                'socket_timeout': 30,
                'force_ipv4': True,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['web'],
                        'skip': ['hls', 'dash'],
                    }
                }
            }
        },
        {
            'name': 'android_basic',
            'opts': {
                'quiet': True,
                'listformats': True,
                'user_agent': random.choice(USER_AGENTS),
                'socket_timeout': 30,
                'force_ipv4': True,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android'],
                        'skip': ['hls'],
                    }
                }
            }
        },
        {
            'name': 'ios_fallback',
            'opts': {
                'quiet': True,
                'listformats': True,
                'user_agent': random.choice(USER_AGENTS),
                'socket_timeout': 45,
                'force_ipv4': True,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['ios'],
                    }
                }
            }
        }
    ]
    
    for strategy in strategies:
        for attempt in range(max_retries):
            try:
                logger.info(f"Trying strategy: {strategy['name']}, attempt: {attempt + 1}")
                
                opts = strategy['opts'].copy()
                
                # Add cookies if available
                if os.path.exists('cookies.txt'):
                    opts['cookiefile'] = 'cookies.txt'
                
                # Add proxy for some attempts
                proxy = os.getenv('PROXY_URL')
                if proxy and attempt > 0:
                    opts['proxy'] = proxy
                
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    formats = info.get('formats', [])
                    
                    if not formats:
                        logger.warning(f"No formats found with {strategy['name']}")
                        continue
                    
                    # Filter formats more carefully
                    video_formats = []
                    audio_formats = []
                    
                    for f in formats:
                        # Skip image formats explicitly
                        if f.get('vcodec') == 'none' and f.get('acodec') == 'none':
                            continue
                        if f.get('format_note') and 'image' in f.get('format_note', '').lower():
                            continue
                        
                        # Audio formats
                        if f.get('acodec') != 'none' and f.get('vcodec') == 'none':
                            audio_formats.append(f)
                        # Video formats with audio
                        elif f.get('vcodec') != 'none' and f.get('acodec') != 'none':
                            video_formats.append(f)
                        # Video-only formats
                        elif f.get('vcodec') != 'none':
                            video_formats.append(f)
                    
                    if video_formats or audio_formats:
                        logger.info(f"✅ Found formats using {strategy['name']}: {len(video_formats)} video, {len(audio_formats)} audio")
                        return info, video_formats, audio_formats, strategy['name']
                        
            except Exception as e:
                error_msg = str(e).lower()
                logger.warning(f"Strategy {strategy['name']} attempt {attempt + 1} failed: {e}")
                
                # Handle specific DNS errors
                if "failed to resolve" in error_msg or "name or service not known" in error_msg:
                    logger.error("DNS resolution failed - network issues detected")
                    await asyncio.sleep(random.uniform(3, 6))
                    continue
                
                if attempt < max_retries - 1:
                    await asyncio.sleep(random.uniform(2, 4))
                continue
    
    raise Exception("Unable to extract video formats - video may be restricted, private, or unavailable")

def get_smart_format_string(format_type, quality, video_formats, audio_formats):
    """Generate smart format string based on actually available formats"""
    
    if format_type == 'mp3' or format_type == 'audio':
        # For audio, prefer standalone audio formats
        if audio_formats:
            # Sort by quality (bitrate or format preference)
            best_audio_formats = []
            for fmt in audio_formats:
                ext = fmt.get('ext', '')
                if ext in ['m4a', 'mp3', 'aac']:
                    best_audio_formats.append(f"format_id={fmt['format_id']}")
            
            if best_audio_formats:
                return '/'.join(best_audio_formats[:3]) + '/bestaudio/best'
            else:
                return 'bestaudio/best'
        else:
            # Fallback to video with audio
            return 'best[height<=720]/best/worst'
    
    else:  # MP4
        if not video_formats:
            return 'best/worst'
        
        # Build format string based on available qualities
        format_options = []
        
        if quality == 'best':
            # Get best available formats
            for fmt in video_formats[:5]:  # Top 5 formats
                if fmt.get('height'):
                    format_options.append(f"format_id={fmt['format_id']}")
            
            format_options.extend(['best[ext=mp4]', 'best[ext=webm]', 'best'])
        else:
            # Quality-specific
            quality_num = quality.replace('p', '')
            
            # Find formats close to requested quality
            suitable_formats = []
            for fmt in video_formats:
                fmt_height = fmt.get('height', 0)
                if fmt_height and abs(fmt_height - int(quality_num)) <= 100:
                    suitable_formats.append(f"format_id={fmt['format_id']}")
            
            if suitable_formats:
                format_options.extend(suitable_formats[:3])
            
            # Add fallback options
            format_options.extend([
                f'best[height<={quality_num}][ext=mp4]',
                f'best[height<={quality_num}]',
                f'worst[height>={int(quality_num)//2}]',
                'best[height<=720]',
                'best'
            ])
        
        return '/'.join(format_options[:8])  # Limit to prevent overly long format strings

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
            [InlineKeyboardButton("360p", callback_data='360'), InlineKeyboardButton("480p", callback_data='480')],
            [InlineKeyboardButton("720p", callback_data='720'), InlineKeyboardButton("1080p", callback_data='1080')],
            [InlineKeyboardButton("Best Available", callback_data='best')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Choose video quality:", reply_markup=reply_markup)

def get_enhanced_ydl_opts(output_file, format_spec, postprocessors, my_hook):
    """Get yt-dlp options with enhanced stability"""
    
    # Download cookies if available
    cookies_available = download_cookies()
    
    # Base options with focus on stability
    ydl_opts = {
        'outtmpl': output_file,
        'format': format_spec,
        'postprocessors': postprocessors,
        'noplaylist': True,
        'progress_hooks': [my_hook],
        
        # Network stability
        'user_agent': random.choice(USER_AGENTS),
        'socket_timeout': 45,
        'retries': 3,
        'fragment_retries': 3,
        'skip_unavailable_fragments': True,
        'force_ipv4': True,
        
        # Conservative extractor settings
        'extractor_args': {
            'youtube': {
                'skip': ['hls'],  # Skip problematic formats
                'player_client': ['web'],  # Start with most stable client
            }
        },
        
        # Error handling
        'ignoreerrors': False,
        'no_warnings': False,
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

    message = await query.edit_message_text(f"🔍 **Checking video availability...**")
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

    try:
        # Get available formats first with better error handling
        await safe_edit_message(context, user_id, message_id, "🔍 **Analyzing video formats...**")
        info, video_formats, audio_formats, successful_strategy = await get_available_formats(url)
        
        # Check if we have usable formats
        if not video_formats and not audio_formats:
            await safe_edit_message(context, user_id, message_id, 
                "❌ **No downloadable content found**\n\n"
                "This video may be:\n"
                "• A live stream or premiere\n"
                "• Region/age restricted\n"
                "• Private or deleted\n"
                "• Contains only images\n\n"
                "Please try a different video.")
            return
        
        # Get video title
        video_title = info.get('title', f'video_{user_id}').replace('/', '_').replace('\\', '_')
        video_title = ''.join(c for c in video_title if c.isalnum() or c in (' ', '-', '_', '.')).strip()[:50]
        
        # Generate optimal format string
        format_spec = get_smart_format_string(format_type, quality, video_formats, audio_formats)
        
        # Set postprocessors
        if format_type == 'mp3' and FFMPEG_AVAILABLE:
            postprocessors = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
        else:
            postprocessors = []
        
        download_type = format_type.upper() if format_type != 'audio' else 'AUDIO'
        await safe_edit_message(context, user_id, message_id,
            f"⏬ **Starting download...**\n\n📹 **Title:** {video_title}\n🎯 **Format:** {download_type}\n📺 **Quality:** {quality if format_type=='mp4' else 'Best Available'}\n🔧 **Method:** {successful_strategy}")
        
        # Get enhanced options
        ydl_opts = get_enhanced_ydl_opts(output_file, format_spec, postprocessors, my_hook)
        
        # Add random delay before download
        await asyncio.sleep(random.uniform(1, 2))
        
        await main_loop.run_in_executor(None, download_with_ytdlp, ydl_opts, url)
        await safe_edit_message(context, user_id, message_id, "📤 **Uploading file...**")

        # Find and upload the downloaded file
        for file in os.listdir(output_dir):
            if file.startswith(f"{user_id}_{timestamp}"):
                file_path = os.path.join(output_dir, file)
                file_size = os.path.getsize(file_path)
                
                if file_size > 50*1024*1024:
                    await safe_edit_message(context, user_id, message_id, 
                        f"❌ **File too large** ({file_size/(1024*1024):.1f}MB > 50MB)\n\n"
                        "Try selecting a lower quality option.")
                else:
                    # Determine appropriate filename
                    file_ext = os.path.splitext(file)[1]
                    filename = f"{video_title}{file_ext}"
                    
                    with open(file_path, 'rb') as f:
                        await asyncio.wait_for(
                            context.bot.send_document(
                                chat_id=user_id, 
                                document=f, 
                                filename=filename
                            ), 
                            timeout=120
                        )
                    
                    await asyncio.wait_for(
                        context.bot.send_message(
                            chat_id=user_id, 
                            text=f"✅ **Download completed!**\n\n"
                                 f"📋 **Format:** {download_type}\n"
                                 f"📊 **Quality:** {quality if format_type=='mp4' else 'Best Available'}\n"
                                 f"📦 **Size:** {file_size/(1024*1024):.1f}MB", 
                            parse_mode='Markdown'
                        ), 
                        timeout=30
                    )
                    
                    try:
                        await context.bot.delete_message(chat_id=user_id, message_id=message_id)
                    except:
                        pass
                
                # Clean up file
                try:
                    os.remove(file_path)
                except:
                    pass
                break
        else:
            await safe_edit_message(context, user_id, message_id, "❌ **Download failed** - No output file generated")
                
    except Exception as e:
        logger.error(f"Download error: {e}")
        error_msg = str(e).lower()
        
        if "network connectivity" in error_msg or "failed to resolve" in error_msg:
            await safe_edit_message(context, user_id, message_id, 
                "❌ **Network Error**\n\n"
                "Connection issues detected. This may be temporary.\n"
                "Please try again in a few minutes.")
        elif "no downloadable content" in error_msg or "only images" in error_msg:
            await safe_edit_message(context, user_id, message_id, 
                "❌ **Content Not Available**\n\n"
                "This video doesn't have downloadable audio/video content.\n"
                "It may be a live stream, image post, or restricted content.")
        elif "ffmpeg" in error_msg:
            await safe_edit_message(context, user_id, message_id, 
                "❌ **Audio Processing Failed**\n\n"
                "Try downloading as MP4 instead, or contact support.")
        elif "sign in" in error_msg or "bot" in error_msg:
            await safe_edit_message(context, user_id, message_id, 
                "❌ **Access Blocked**\n\n"
                "YouTube has temporarily blocked this request.\n"
                "Please try again in 10-15 minutes.")
        else:
            await safe_edit_message(context, user_id, message_id, 
                f"❌ **Download Failed**\n\n"
                f"Error: {str(e)[:100]}...\n\n"
                f"Please try a different video or quality setting.")
            
    finally:
        # Clean up
        user_messages.pop(user_id, None)
        last_percent.pop(user_id, None)
        last_update_time.pop(user_id, None)
        user_formats.pop(user_id, None)

def download_with_ytdlp(ydl_opts, url):
    """Download with improved retry mechanism"""
    max_retries = 2
    
    for attempt in range(max_retries):
        try:
            # Add delay between attempts
            if attempt > 0:
                time.sleep(random.uniform(3, 6))
                # Try different client on retry
                ydl_opts['user_agent'] = random.choice(USER_AGENTS)
                if 'extractor_args' in ydl_opts:
                    ydl_opts['extractor_args']['youtube']['player_client'] = ['android', 'web'][attempt % 2]
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            return  # Success
            
        except Exception as e:
            logger.error(f"Download attempt {attempt + 1} failed: {e}")
            if attempt == max_retries - 1:
                raise e

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

    # Log system status
    logger.info(f"FFmpeg available: {FFMPEG_AVAILABLE}")
    logger.info(f"FFprobe available: {FFPROBE_AVAILABLE}")
    logger.info(f"Network connectivity: {check_network()}")

    request = HTTPXRequest(
        connection_pool_size=8, 
        read_timeout=60, 
        write_timeout=60, 
        connect_timeout=15, 
        pool_timeout=15
    )
    
    app = ApplicationBuilder().token(TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(choose_quality, pattern='^(mp3|mp4)$'))
    app.add_handler(CallbackQueryHandler(download_video, pattern='^(360|480|720|1080|best)$'))
    app.add_handler(CallbackQueryHandler(handle_special_callbacks, pattern='^(audio_original|cancel)$'))
    
    logger.info("🤖 Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()