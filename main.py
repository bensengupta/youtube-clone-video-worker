import json
import mimetypes
import os
import subprocess
import sys

import boto3
import requests

TASK_ATTEMPT = os.getenv("CLOUD_RUN_TASK_ATTEMPT", 0)

CLOUDFLARE_R2_ACCOUNT_ID = os.getenv("CLOUDFLARE_R2_ACCOUNT_ID")
CLOUDFLARE_R2_ACCESS_KEY_ID = os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID")
CLOUDFLARE_R2_SECRET_ACCESS_KEY = os.getenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY")
CLOUDFLARE_R2_BUCKET_NAME = os.getenv("CLOUDFLARE_R2_BUCKET_NAME")

VIDEO_ID = os.getenv("VIDEO_ID")
CALLBACK_URL = os.getenv("CALLBACK_URL")

upload_key = f"video/{VIDEO_ID}"

s3 = boto3.client(
    service_name="s3",
    endpoint_url=f"https://{CLOUDFLARE_R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    aws_access_key_id=CLOUDFLARE_R2_ACCESS_KEY_ID,
    aws_secret_access_key=CLOUDFLARE_R2_SECRET_ACCESS_KEY,
    region_name="auto",
)


class VideoMetadata:
    def __init__(
        self, width: int, height: int, frame_rate: int, duration_seconds: float
    ):
        self.width = width
        self.height = height
        self.frame_rate = frame_rate
        self.duration_seconds = duration_seconds


class VideoQuality:
    def __init__(
        self, name: str, width: int, height: int, filepath: str, ffmpeg_flags: str
    ):
        self.name = name
        self.width = width
        self.height = height
        self.filepath = filepath
        self.ffmpeg_flags = ffmpeg_flags


def download_input_mp4():
    print("\n===== Downloading input.mp4 =====")

    with open("input.mp4", "wb") as f:
        s3.download_fileobj(
            CLOUDFLARE_R2_BUCKET_NAME,
            f"video/{VIDEO_ID}/original",
            f,
        )


def create_thumbnail():
    print("\n===== Creating thumbnail =====")

    command_parts = [
        "./ffmpeg/ffmpeg",
        "-i input.mp4",
        "-ss 00:00:01.000",
        "-vframes 1",
        "-vf scale=320:180",
        "out/thumbnail.jpg",
    ]

    command = " ".join(command_parts)

    print(command)
    os.system(command)


def gather_metadata():
    print("\n===== Gathering metadata =====")

    command = [
        "./ffmpeg/ffprobe",
        "-v error",
        "-select_streams v:0",
        "-show_entries stream=width,height,r_frame_rate",
        "-show_entries format=duration",
        "-of default=noprint_wrappers=1:nokey=1",
        "input.mp4",
    ]

    output = os.popen(" ".join(command)).read().split("\n")
    print("ffprobe output", output)
    width = int(output[0])
    height = int(output[1])
    frame_rate = int(output[2].split("/")[0])
    duration_seconds = float(output[3])

    return VideoMetadata(width, height, frame_rate, duration_seconds)


def determine_qualities(metadata: VideoMetadata) -> list[VideoQuality]:
    print("\n===== Determining qualities =====")

    qualities = [
        # 1080p
        VideoQuality(
            name="1080p",
            width=1920,
            height=1080,
            filepath="out/1080p.mp4",
            ffmpeg_flags="-vf scale=1920:1080",
        ),
        # 720p
        VideoQuality(
            name="720p",
            width=1280,
            height=720,
            filepath="out/720p.mp4",
            ffmpeg_flags="-vf scale=1280:720",
        ),
        # 480p (minimum resolution)
        VideoQuality(
            name="480p",
            width=0,
            height=0,
            filepath="out/480p.mp4",
            ffmpeg_flags="-vf scale=854:480",
        ),
    ]

    def filter_keep_lower_resolutions(q: VideoQuality):
        return q.width <= metadata.width or q.height <= metadata.height

    qualities = list(filter(filter_keep_lower_resolutions, qualities))

    return qualities


def run_transcode(metadata: VideoMetadata, qualities: list[VideoQuality]):
    print("\n===== Running transcode =====")

    frame_rate = int(min(metadata.frame_rate, 60))

    common_params = [
        "-c:v h264",  # video codec H.264
        "-profile:v main",  # H.264 profile main
        "-level:v 4.0",  # H.264 level 4.0
        "-an",  # remove audio
        f"-r {frame_rate}",  # keep framerate (max 60fps)
        f"-g {frame_rate * 2}",  # keyframe interval (max 2 seconds)
        "-crf 22",  # constant rate factor, lower is better quality
        "-pix_fmt yuv420p",
        "-movflags faststart",
        "-map_metadata -1",  # remove metadata
    ]

    def map_to_subcommand(q: VideoQuality):
        return " ".join([q.ffmpeg_flags, *common_params, q.filepath])

    subcommands = list(map(map_to_subcommand, qualities))

    subcommands.append("-c:a aac -b:a 128k -map_metadata -1 out/audio.mp4")

    command = f"./ffmpeg/ffmpeg -i input.mp4 {' '.join(subcommands)}"

    print(command)

    os.system(command)


def generate_manifest(qualities: list[VideoQuality]):
    print("\n===== Generating manifest =====")

    def map_to_dash_stream(q: VideoQuality):
        return f"input={q.filepath},stream=video,output=out/video_{q.name}.mp4,playlist_name=out/video_{q.name}.m3u8,iframe_playlist_name=out/video_{q.name}_iframe.m3u8"

    subcommands = list(map(map_to_dash_stream, qualities))

    command_parts = [
        "./shaka-packager/packager",
        # audio stream
        f"input=out/audio.mp4,stream=audio,output=out/audio.mp4,playlist_name=out/audio.m3u8,hls_group_id=audio,hls_name=ENGLISH",
        *subcommands,
        "--hls_master_playlist_output out/manifest.m3u8",
        "--mpd_output out/manifest.mpd",
    ]

    command = " ".join(command_parts)

    print(command)

    os.system(command)

    files_to_upload = [
        "out/manifest.mpd",
        "out/manifest.m3u8",
        "out/thumbnail.jpg",
        "out/audio.mp4",
        "out/audio.m3u8",
        *map(lambda q: f"out/video_{q.name}.mp4", qualities),
        *map(lambda q: f"out/video_{q.name}.m3u8", qualities),
        *map(lambda q: f"out/video_{q.name}_iframe.m3u8", qualities),
    ]

    return files_to_upload


def get_base_filename(filepath: str):
    return filepath.split("/")[-1]


def get_file_size(filepath: str):
    return os.path.getsize(filepath)


def get_file_mimetype(filepath: str):
    if filepath.endswith(".mpd"):
        return "application/dash+xml"
    return mimetypes.guess_type(filepath)[0]


def upload_files(filepaths: list[str]):
    print("\n===== Uploading output =====")

    print(filepaths)

    for filepath in filepaths:
        with open(filepath, "rb") as f:
            upload_key = f"video/{VIDEO_ID}/{get_base_filename(filepath)}"
            mimetype = get_file_mimetype(filepath)

            print("Uploading", filepath, "as", upload_key, "with mimetype", mimetype)

            s3.upload_fileobj(
                f,
                CLOUDFLARE_R2_BUCKET_NAME,
                upload_key,
                ExtraArgs={"ContentType": mimetype},
            )


def send_completion_callback(metadata: VideoMetadata):
    print("\n===== Send completion callback =====")
    if not CALLBACK_URL:
        print("Skipped")
        return
    print("Sending callback to", CALLBACK_URL)
    requests.post(
        f"{CALLBACK_URL}/complete",
        json={
            "video_id": VIDEO_ID,
            "duration": int(metadata.duration_seconds),
        },
    )


def main():
    print("=== running ls ===")
    os.system("ls")
    print("=== running ffmpeg -version ===")
    os.system("./ffmpeg/ffmpeg -version")
    print("=== running ffmpeg -version in subprocess  ===")
    process = subprocess.Popen(
        ["./ffmpeg/ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    stdout, stderr = process.communicate()
    print(stdout)

    return
    download_input_mp4()
    create_thumbnail()
    metadata = gather_metadata()
    qualities = determine_qualities(metadata)
    run_transcode(metadata, qualities)
    files_to_upload = generate_manifest(qualities)
    upload_files(files_to_upload)
    send_completion_callback(metadata)


# Start script
if __name__ == "__main__":
    print(f"Starting processing of video {VIDEO_ID}")
    try:
        main()
    except Exception as err:
        print(f"Video Worker failed (attempt #{TASK_ATTEMPT})")
        print(err)
        sys.exit(1)  # Retry Job Task by exiting the process
