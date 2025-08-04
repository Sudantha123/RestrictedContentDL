# Copyright (C) @TheSmartBisnu
# Channel: https://t.me/itsSmartDev

import os
from time import time
from PIL import Image
from logger import LOGGER
from typing import Optional
from asyncio.subprocess import PIPE
from asyncio import create_subprocess_exec, create_subprocess_shell, wait_for

from pyleaves import Leaves
from pyrogram.parser import Parser
from pyrogram.utils import get_channel_id
from pyrogram.types import (
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    InputMediaAudio,
    Voice,
)

from helpers.files import (
    fileSizeLimit,
    cleanup_download
)

from helpers.msg import (
    get_parsed_msg
)

# Progress bar template
PROGRESS_BAR = """
Percentage: {percentage:.2f}% | {current}/{total}
Speed: {speed}/s
Estimated Time Left: {est_time} seconds
"""

async def cmd_exec(cmd, shell=False):
    if shell:
        proc = await create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)
    else:
        proc = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
    stdout, stderr = await proc.communicate()
    try:
        stdout = stdout.decode().strip()
    except:
        stdout = "Unable to decode the response!"
    try:
        stderr = stderr.decode().strip()
    except:
        stderr = "Unable to decode the error!"
    return stdout, stderr, proc.returncode


async def get_media_info(path):
    try:
        # First try to get format info
        result = await cmd_exec([
            "ffprobe", "-hide_banner", "-loglevel", "error",
            "-print_format", "json", "-show_format", "-show_streams", path,
        ])
    except Exception as e:
        LOGGER(__name__).warning(f"Get Media Info: {e}. File: {path}")
        return 0, None, None
    
    if result[0] and result[2] == 0:
        try:
            import json
            data = json.loads(result[0])
            
            # Try to get duration from format first
            duration = 0
            if "format" in data and "duration" in data["format"]:
                duration = round(float(data["format"]["duration"]))
            
            # If no duration in format, try streams
            if duration == 0 and "streams" in data:
                for stream in data["streams"]:
                    if stream.get("codec_type") == "video" and "duration" in stream:
                        duration = round(float(stream["duration"]))
                        break
            
            # Get metadata tags
            tags = data.get("format", {}).get("tags", {})
            artist = tags.get("artist") or tags.get("ARTIST") or tags.get("Artist")
            title = tags.get("title") or tags.get("TITLE") or tags.get("Title")
            
            return duration, artist, title
            
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            LOGGER(__name__).warning(f"Failed to parse media info: {e}")
            return 0, None, None
    
    return 0, None, None


async def get_video_thumbnail(video_file, duration):
    # Ensure Assets directory exists
    os.makedirs("Assets", exist_ok=True)
    output = os.path.join("Assets", "video_thumb.jpg")
    
    if duration is None:
        duration = (await get_media_info(video_file))[0]
    
    if not duration or duration <= 0:
        duration = 3
    
    # Try multiple timestamp positions for better thumbnail
    timestamps = [duration // 2, duration // 4, duration // 3, 1]
    
    for timestamp in timestamps:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", str(timestamp), "-i", video_file,
            "-vf", "thumbnail,scale=320:240", "-q:v", "2", "-frames:v", "1",
            "-threads", str(max(1, os.cpu_count() // 2)), "-y", output,
        ]
        try:
            _, err, code = await wait_for(cmd_exec(cmd), timeout=30)
            if code == 0 and os.path.exists(output) and os.path.getsize(output) > 0:
                return output
        except Exception as e:
            LOGGER(__name__).warning(f"Thumbnail generation failed at {timestamp}s: {e}")
            continue
    
    # If all attempts fail, try a simple frame extraction
    try:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", video_file, "-vf", "scale=320:240", "-q:v", "2", 
            "-frames:v", "1", "-y", output
        ]
        _, err, code = await wait_for(cmd_exec(cmd), timeout=30)
        if code == 0 and os.path.exists(output) and os.path.getsize(output) > 0:
            return output
    except Exception as e:
        LOGGER(__name__).warning(f"Final thumbnail attempt failed: {e}")
    
    return None


# Generate progress bar for downloading/uploading
def progressArgs(action: str, progress_message, start_time):
    return (action, progress_message, start_time, PROGRESS_BAR, "▓", "░")


async def send_media(
    bot, message, media_path, media_type, caption, progress_message, start_time
):
    file_size = os.path.getsize(media_path)

    if not await fileSizeLimit(file_size, message, "upload"):
        return

    progress_args = progressArgs("📥 Uploading Progress", progress_message, start_time)
    LOGGER(__name__).info(f"Uploading media: {media_path} ({media_type})")

    if media_type == "photo":
        await message.reply_photo(
            media_path,
            caption=caption or "",
            progress=Leaves.progress_for_pyrogram,
            progress_args=progress_args,
        )
    elif media_type == "video":
        # Clean up any existing thumbnail
        thumb_path = os.path.join("Assets", "video_thumb.jpg")
        if os.path.exists(thumb_path):
            os.remove(thumb_path)
        
        # Get video info
        duration, _, _ = await get_media_info(media_path)
        
        # Generate thumbnail
        thumb = await get_video_thumbnail(media_path, duration)
        
        # Get video dimensions
        width, height = 480, 320  # Default values
        
        if thumb and os.path.exists(thumb):
            try:
                with Image.open(thumb) as img:
                    width, height = img.size
            except Exception as e:
                LOGGER(__name__).warning(f"Failed to get thumbnail dimensions: {e}")
        
        # Try to get actual video dimensions if thumbnail failed
        if not thumb:
            try:
                result = await cmd_exec([
                    "ffprobe", "-hide_banner", "-loglevel", "error",
                    "-select_streams", "v:0", "-show_entries", "stream=width,height",
                    "-of", "csv=s=x:p=0", media_path
                ])
                if result[0] and result[2] == 0:
                    dimensions = result[0].strip().split('x')
                    if len(dimensions) == 2:
                        width, height = int(dimensions[0]), int(dimensions[1])
            except Exception as e:
                LOGGER(__name__).warning(f"Failed to get video dimensions: {e}")

        await message.reply_video(
            media_path,
            duration=duration if duration > 0 else None,
            width=width,
            height=height,
            thumb=thumb,
            caption=caption or "",
            progress=Leaves.progress_for_pyrogram,
            progress_args=progress_args,
        )
    elif media_type == "audio":
        duration, artist, title = await get_media_info(media_path)
        await message.reply_audio(
            media_path,
            duration=duration,
            performer=artist,
            title=title,
            caption=caption or "",
            progress=Leaves.progress_for_pyrogram,
            progress_args=progress_args,
        )
    elif media_type == "document":
        await message.reply_document(
            media_path,
            caption=caption or "",
            progress=Leaves.progress_for_pyrogram,
            progress_args=progress_args,
        )


async def processMediaGroup(chat_message, bot, message):
    media_group_messages = await chat_message.get_media_group()
    valid_media = []
    temp_paths = []
    invalid_paths = []

    start_time = time()
    progress_message = await message.reply("📥 Downloading media group...")
    LOGGER(__name__).info(
        f"Downloading media group with {len(media_group_messages)} items..."
    )

    for msg in media_group_messages:
        if msg.photo or msg.video or msg.document or msg.audio:
            try:
                media_path = await msg.download(
                    progress=Leaves.progress_for_pyrogram,
                    progress_args=progressArgs(
                        "📥 Downloading Progress", progress_message, start_time
                    ),
                )
                temp_paths.append(media_path)

                if msg.photo:
                    valid_media.append(
                        InputMediaPhoto(
                            media=media_path,
                            caption=await get_parsed_msg(
                                msg.caption or "", msg.caption_entities
                            ),
                        )
                    )
                elif msg.video:
                    valid_media.append(
                        InputMediaVideo(
                            media=media_path,
                            caption=await get_parsed_msg(
                                msg.caption or "", msg.caption_entities
                            ),
                        )
                    )
                elif msg.document:
                    valid_media.append(
                        InputMediaDocument(
                            media=media_path,
                            caption=await get_parsed_msg(
                                msg.caption or "", msg.caption_entities
                            ),
                        )
                    )
                elif msg.audio:
                    valid_media.append(
                        InputMediaAudio(
                            media=media_path,
                            caption=await get_parsed_msg(
                                msg.caption or "", msg.caption_entities
                            ),
                        )
                    )

            except Exception as e:
                LOGGER(__name__).info(f"Error downloading media: {e}")
                if media_path and os.path.exists(media_path):
                    invalid_paths.append(media_path)
                continue

    LOGGER(__name__).info(f"Valid media count: {len(valid_media)}")

    if valid_media:
        try:
            await bot.send_media_group(chat_id=message.chat.id, media=valid_media)
            await progress_message.delete()
        except Exception:
            await message.reply(
                "**❌ Failed to send media group, trying individual uploads**"
            )
            for media in valid_media:
                try:
                    if isinstance(media, InputMediaPhoto):
                        await bot.send_photo(
                            chat_id=message.chat.id,
                            photo=media.media,
                            caption=media.caption,
                        )
                    elif isinstance(media, InputMediaVideo):
                        await bot.send_video(
                            chat_id=message.chat.id,
                            video=media.media,
                            caption=media.caption,
                        )
                    elif isinstance(media, InputMediaDocument):
                        await bot.send_document(
                            chat_id=message.chat.id,
                            document=media.media,
                            caption=media.caption,
                        )
                    elif isinstance(media, InputMediaAudio):
                        await bot.send_audio(
                            chat_id=message.chat.id,
                            audio=media.media,
                            caption=media.caption,
                        )
                    elif isinstance(media, Voice):
                        await bot.send_voice(
                            chat_id=message.chat.id,
                            voice=media.media,
                            caption=media.caption,
                        )
                except Exception as individual_e:
                    await message.reply(
                        f"Failed to upload individual media: {individual_e}"
                    )

            await progress_message.delete()

        for path in temp_paths + invalid_paths:
            cleanup_download(path)
        return True

    await progress_message.delete()
    await message.reply("❌ No valid media found in the media group.")
    for path in invalid_paths:
        cleanup_download(path)
    return False
