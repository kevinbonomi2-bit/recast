"""Audio and video via FFmpeg."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from .. import paths
from ..formats import ext_of
from ..paths import output_for
from .core import Cancelled, ConversionError, Context, op

AUDIO, VIDEO = "audio", "video"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ffmpeg() -> str:
    exe = paths.ffmpeg()
    if not exe:
        raise ConversionError("FFmpeg isn't installed. Install it with: winget install Gyan.FFmpeg")
    return exe


def probe(src: Path) -> dict:
    exe = paths.ffprobe()
    if not exe:
        return {}
    out = subprocess.run([exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                          str(src)], capture_output=True, text=True, creationflags=NO_WINDOW)
    try:
        return json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def duration(info: dict) -> float:
    try:
        return float(info.get("format", {}).get("duration", 0) or 0)
    except ValueError:
        return 0.0


def codecs(info: dict) -> tuple[str | None, str | None]:
    v = a = None
    for s in info.get("streams", []):
        if s.get("codec_type") == "video" and v is None and not s.get("disposition", {}).get("attached_pic"):
            v = s.get("codec_name")
        elif s.get("codec_type") == "audio" and a is None:
            a = s.get("codec_name")
    return v, a


def run_ffmpeg(args: list[str], total: float, ctx: Context) -> None:
    """Run ffmpeg, mapping its -progress output onto ctx; kill it on cancel."""
    cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-progress", "pipe:1", "-nostats", *args]
    # stderr goes to a file: a full stderr pipe would block ffmpeg while we read stdout.
    with tempfile.TemporaryFile() as errfile:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errfile, text=True,
                                encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        try:
            for line in proc.stdout:
                if ctx.cancel_event.is_set():
                    proc.kill()
                    raise Cancelled()
                if line.startswith("out_time_us=") and total > 0:
                    try:
                        ctx.progress(int(line.split("=", 1)[1]) / 1e6 / total)
                    except ValueError:
                        pass
            if proc.wait() != 0:
                errfile.seek(0)
                err = errfile.read().decode("utf-8", "replace")
                last = [l for l in err.strip().splitlines() if l.strip()][-1:] or ["unknown error"]
                raise ConversionError(f"FFmpeg failed: {last[0]}")
        finally:
            if proc.poll() is None:
                proc.kill()


def _transcode(src: Path, dest: Path, args: list[str], ctx: Context, info: dict | None = None) -> list[Path]:
    info = info if info is not None else probe(src)
    try:
        run_ffmpeg(["-i", str(src), *args, str(dest)], duration(info), ctx)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return [dest]


# ---- audio -----------------------------------------------------------------

AUDIO_ARGS = {
    "mp3": ["-vn", "-c:a", "libmp3lame", "-q:a", "2"],
    "m4a": ["-vn", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"],
    "wav": ["-vn", "-c:a", "pcm_s16le"],
    "flac": ["-vn", "-c:a", "flac"],
    "ogg": ["-vn", "-c:a", "libvorbis", "-q:a", "5"],
    "opus": ["-vn", "-c:a", "libopus", "-b:a", "128k"],
    "aiff": ["-vn", "-c:a", "pcm_s16be"],
    "wma": ["-vn", "-c:a", "wmav2", "-b:a", "192k"],
}
AUDIO_LABELS = {"mp3": "MP3", "m4a": "M4A", "wav": "WAV", "flac": "FLAC", "ogg": "OGG",
                "opus": "Opus", "aiff": "AIFF", "wma": "WMA"}

for _t, _args in AUDIO_ARGS.items():
    op(f"audio.to.{_t}", AUDIO_LABELS[_t], "convert", AUDIO, target=_t)(
        lambda src, ctx, t=_t, a=_args: _transcode(src, output_for(src, t), ["-map", "0:a:0", *a], ctx))

# Videos can be converted straight to audio formats too.
for _t in ("mp3", "m4a", "wav"):
    op(f"video.to.{_t}", AUDIO_LABELS[_t], "convert", VIDEO, target=_t)(
        lambda src, ctx, t=_t: _transcode(src, output_for(src, t), ["-map", "0:a:0", *AUDIO_ARGS[t]], ctx))


@op("audio.compress", "Compress", "tools", AUDIO, hint="128 kbps MP3")
def audio_compress(src: Path, ctx: Context) -> list[Path]:
    return _transcode(src, output_for(src, "mp3", "compressed"),
                      ["-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-b:a", "128k"], ctx)


@op("audio.normalize", "Normalize volume", "tools", AUDIO, hint="Even out loudness (EBU R128)")
def audio_normalize(src: Path, ctx: Context) -> list[Path]:
    t = ext_of(src) if ext_of(src) in AUDIO_ARGS else "m4a"
    return _transcode(src, output_for(src, t, "normalized"),
                      ["-map", "0:a:0", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", *AUDIO_ARGS[t]], ctx)


# ---- video -----------------------------------------------------------------

# Codecs each container can hold without re-encoding.
REMUX_OK = {
    "mp4": ({"h264", "hevc", "av1", "mpeg4"}, {"aac", "mp3", "ac3", "eac3", "opus", "alac", "flac"}),
    "mov": ({"h264", "hevc", "prores", "mpeg4", "mjpeg"}, {"aac", "mp3", "alac", "pcm_s16le", "pcm_s24le"}),
    "mkv": ({"h264", "hevc", "av1", "vp8", "vp9", "mpeg4", "mpeg2video", "prores"},
            {"aac", "mp3", "ac3", "eac3", "opus", "vorbis", "flac", "dts", "alac", "pcm_s16le"}),
    "webm": ({"vp8", "vp9", "av1"}, {"opus", "vorbis"}),
}
X264 = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]
EVEN = ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
VIDEO_ARGS = {
    "mp4": [*X264, *EVEN, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"],
    "mov": [*X264, *EVEN, "-c:a", "aac", "-b:a", "192k"],
    "mkv": [*X264, *EVEN, "-c:a", "aac", "-b:a", "192k"],
    "webm": ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1", "-cpu-used", "4",
             "-deadline", "good", "-c:a", "libopus", "-b:a", "128k"],
    "avi": ["-c:v", "mpeg4", "-q:v", "3", "-c:a", "libmp3lame", "-q:a", "3"],
    "wmv": ["-c:v", "wmv2", "-q:v", "3", "-c:a", "wmav2", "-b:a", "192k"],
}
VIDEO_LABELS = {"mp4": "MP4", "mov": "MOV", "mkv": "MKV", "webm": "WebM", "avi": "AVI", "wmv": "WMV"}


def convert_video(src: Path, target: str, ctx: Context) -> list[Path]:
    info = probe(src)
    v, a = codecs(info)
    dest = output_for(src, target)
    ok = REMUX_OK.get(target)
    if ok and v in ok[0] and (a is None or a in ok[1]):
        # Same streams, new container: seconds instead of minutes.
        extra = ["-movflags", "+faststart"] if target in ("mp4", "mov") else []
        return _transcode(src, dest, ["-map", "0:v:0", "-map", "0:a?", "-c", "copy", *extra], ctx, info)
    return _transcode(src, dest, ["-map", "0:v:0", "-map", "0:a?", *VIDEO_ARGS[target]], ctx, info)


for _t in VIDEO_ARGS:
    op(f"video.to.{_t}", VIDEO_LABELS[_t], "convert", VIDEO, target=_t)(
        lambda src, ctx, t=_t: convert_video(src, t, ctx))


def make_gif(src: Path, ctx: Context, tag: str = "") -> list[Path]:
    fps = ctx.options.get("gif_fps", 15)
    width = ctx.options.get("gif_width", 640)
    graph = (f"fps={fps},scale='min({width},iw)':-2:flags=lanczos,split[a][b];"
             f"[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")
    return _transcode(src, output_for(src, "gif", tag), ["-filter_complex", graph, "-loop", "0"], ctx)


op("video.to.gif", "GIF", "convert", VIDEO, target="gif")(lambda src, ctx: make_gif(src, ctx))

# Animated GIFs are images, but MP4/WebM are the useful targets for them.
op("gif.to.mp4", "MP4", "convert", "image", target="mp4", only_ext={"gif"})(
    lambda src, ctx: _transcode(src, output_for(src, "mp4"),
                                ["-movflags", "+faststart", "-pix_fmt", "yuv420p", *EVEN, "-c:v", "libx264",
                                 "-crf", "20"], ctx))


@op("video.compress", "Compress", "tools", VIDEO, hint="H.264, up to 1080p, smaller file")
def video_compress(src: Path, ctx: Context) -> list[Path]:
    crf = {"light": 23, "balanced": 27, "strong": 31}.get(ctx.options.get("compress_level", "balanced"), 27)
    return _transcode(src, output_for(src, "mp4", "compressed"),
                      ["-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
                       "-vf", "scale='if(gt(ih,1080),-2,trunc(iw/2)*2)':'if(gt(ih,1080),1080,trunc(ih/2)*2)'",
                       "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"], ctx)


@op("video.mute", "Remove audio", "tools", VIDEO)
def video_mute(src: Path, ctx: Context) -> list[Path]:
    t = ext_of(src) if ext_of(src) in VIDEO_ARGS else "mp4"
    return _transcode(src, output_for(src, t, "muted"), ["-map", "0:v", "-c", "copy", "-an"], ctx)


@op("video.extract_audio", "Extract audio", "tools", VIDEO, hint="Keep the original audio quality")
def video_extract_audio(src: Path, ctx: Context) -> list[Path]:
    info = probe(src)
    _, a = codecs(info)
    if a is None:
        raise ConversionError(f"{src.name} has no audio track")
    copy_as = {"aac": "m4a", "mp3": "mp3", "opus": "opus", "vorbis": "ogg", "flac": "flac"}.get(a)
    if copy_as:
        return _transcode(src, output_for(src, copy_as), ["-map", "0:a:0", "-vn", "-c:a", "copy"], ctx, info)
    return _transcode(src, output_for(src, "m4a"), ["-map", "0:a:0", *AUDIO_ARGS["m4a"]], ctx, info)


op("video.make_gif", "Make GIF", "tools", VIDEO, hint="640px wide, 15 fps")(
    lambda src, ctx: make_gif(src, ctx))


@op("video.rotate", "Rotate right", "tools", VIDEO)
def video_rotate(src: Path, ctx: Context) -> list[Path]:
    t = ext_of(src) if ext_of(src) in ("mp4", "mov", "mkv") else "mp4"
    return _transcode(src, output_for(src, t, "rotated"),
                      ["-map", "0:v:0", "-map", "0:a?", *X264, "-vf", "transpose=1", "-c:a", "copy"], ctx)


# ---- tools that open a window first ------------------------------------------

def _clock(seconds: float) -> str:
    return f"{seconds:.3f}"


@op("video.trim", "Trim…", "tools", VIDEO, asks=True, hint="Keep the part between two points")
def video_trim(src: Path, ctx: Context) -> list[Path]:
    """options: start, end in seconds. Re-encodes so the cut lands on the exact frame."""
    start, end = ctx.options["start"], ctx.options["end"]
    if end <= start:
        raise ConversionError("The end has to come after the start")
    t = ext_of(src) if ext_of(src) in ("mp4", "mov", "mkv") else "mp4"
    dest = output_for(src, t, "trimmed")
    args = ["-ss", _clock(start), "-to", _clock(end), "-i", str(src), "-map", "0:v:0", "-map", "0:a?", *X264,
            "-c:a", "aac", "-b:a", "192k"]
    if t in ("mp4", "mov"):
        args += ["-movflags", "+faststart"]
    try:
        run_ffmpeg([*args, str(dest)], end - start, ctx)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return [dest]


@op("audio.trim", "Trim…", "tools", AUDIO, asks=True, hint="Keep the part between two points")
def audio_trim(src: Path, ctx: Context) -> list[Path]:
    start, end = ctx.options["start"], ctx.options["end"]
    if end <= start:
        raise ConversionError("The end has to come after the start")
    t = ext_of(src) if ext_of(src) in AUDIO_ARGS else "m4a"
    dest = output_for(src, t, "trimmed")
    try:
        run_ffmpeg(["-ss", _clock(start), "-to", _clock(end), "-i", str(src), "-map", "0:a:0", *AUDIO_ARGS[t],
                    str(dest)], end - start, ctx)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return [dest]


@op("video.compress_to", "Compress to size…", "tools", VIDEO, asks=True, hint="Choose a target size or reduction")
def video_compress_to(src: Path, ctx: Context) -> list[Path]:
    """Two-pass H.264 at the bitrate that lands on the target size (options: ratio, max_height)."""
    return encode_to_size(src, src.stat().st_size * ctx.options.get("ratio", 0.5), "compressed", ctx)


# Discord's upload limit in MB (20 free, 50 Nitro Basic, 1000 Nitro, as of Oct 2026); set by the app from Settings.
DISCORD_LIMIT_MB = 20


def limit_text(mb: int) -> str:
    """"20 MB", "1 GB"."""
    return f"{mb // 1000} GB" if mb >= 1000 and mb % 1000 == 0 else f"{mb} MB"


@op("video.discord", "For Discord", "tools", VIDEO, copy_result=True,
    hint="Fit under your Discord upload limit, then copy it for pasting")
def video_discord(src: Path, ctx: Context) -> list[Path]:
    limit = ctx.options.get("discord_limit_mb", DISCORD_LIMIT_MB) * 1_000_000
    if src.stat().st_size <= limit * 0.98 and ext_of(src) in ("mp4", "webm", "mov"):
        ctx.progress(1.0)
        return [src]  # already fits: nothing to encode, it just goes on the clipboard
    return encode_to_size(src, limit * 0.94, "discord", ctx)  # margin for bitrate overshoot


def encode_to_size(src: Path, goal: float, tag: str, ctx: Context) -> list[Path]:
    info = probe(src)
    dur = duration(info)
    if dur <= 0:
        raise ConversionError(f"Couldn't read the length of {src.name}")
    total_kbps = goal * 8 / dur / 1000 * 0.97  # 3% container overhead
    audio_kbps = 128 if total_kbps > 1200 else 96 if total_kbps > 400 else 64
    video_kbps = max(60, int(total_kbps - audio_kbps))
    height = ctx.options.get("max_height") or 0
    if not height:
        # Tight budgets look better smaller than blocky: pick a height the bitrate can carry.
        height = 360 if video_kbps < 300 else 480 if video_kbps < 700 else 720 if video_kbps < 1500 else 0
    vf = (f"scale=-2:'min({height},trunc(ih/2)*2)'" if height else "scale=trunc(iw/2)*2:trunc(ih/2)*2")
    dest = output_for(src, "mp4", tag)
    with tempfile.TemporaryDirectory(prefix="recast-") as tmp:
        log = str(Path(tmp) / "pass")
        common = ["-i", str(src), "-map", "0:v:0", "-c:v", "libx264", "-preset", "medium", "-b:v", f"{video_kbps}k",
                  "-vf", vf, "-pix_fmt", "yuv420p", "-passlogfile", log]
        try:
            run_ffmpeg([*common, "-pass", "1", "-an", "-f", "null", "NUL"], dur, ctx.sub(0, 0.45))
            run_ffmpeg([*common, "-pass", "2", "-map", "0:a?", "-c:a", "aac", "-b:a", f"{audio_kbps}k",
                        "-movflags", "+faststart", str(dest)], dur, ctx.sub(0.45, 1))
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
    return [dest]


@op("audio.compress_to", "Compress to size…", "tools", AUDIO, asks=True, hint="Choose a target size or reduction")
def audio_compress_to(src: Path, ctx: Context) -> list[Path]:
    info = probe(src)
    dur = duration(info)
    if dur <= 0:
        raise ConversionError(f"Couldn't read the length of {src.name}")
    kbps = int(src.stat().st_size * ctx.options.get("ratio", 0.5) * 8 / dur / 1000)
    kbps = max(32, min(320, kbps))
    return _transcode(src, output_for(src, "mp3", "compressed"),
                      ["-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-b:a", f"{kbps}k"], ctx, info)
