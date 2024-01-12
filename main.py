import json
import mimetypes
import os
import sys
from typing import TypedDict

import requests

TASK_ATTEMPT = os.getenv("CLOUD_RUN_TASK_ATTEMPT", 0)

DOWNLOAD_URL = os.getenv("DOWNLOAD_URL")

CALLBACK_URL = os.getenv("CALLBACK_URL")


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

    resp = requests.get(DOWNLOAD_URL)
    with open("input.mp4", "wb") as f:
        f.write(resp.content)


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
    return mimetypes.guess_type(filepath)[0]


class CallbackPostResponse(TypedDict):
    multipart_upload_id: str
    upload_key: str
    presigned_urls: list[str]
    part_size: int


def upload_files(filepaths: list[str]):
    print("\n===== Uploading output =====")

    print(filepaths)

    for filepath in filepaths:
        req_json = {
            "file_name": get_base_filename(filepath),
            "file_size": get_file_size(filepath),
            "content_type": get_file_mimetype(filepath),
        }

        print("Requesting upload of", filepath, ":", req_json)

        resp = requests.post(f"{CALLBACK_URL}/request-file-upload", json=req_json)

        if not resp.ok:
            raise Exception(f"Upload request failed ({resp.status_code}): {resp.text}")

        resp_json: CallbackPostResponse = resp.json()

        part_data = []

        with open(filepath, "rb") as f:
            print("Reading", filepath)
            for idx, presigned_url in enumerate(resp_json["presigned_urls"]):
                print("Uploading part", idx + 1, "of", filepath)
                part_resp = requests.put(
                    presigned_url, data=f.read(resp_json["part_size"])
                )
                part_data.append(
                    {
                        "PartNumber": idx + 1,
                        "ETag": part_resp.headers["ETag"],
                    }
                )

        resp = requests.post(
            f"{CALLBACK_URL}/finish-file-upload",
            json={
                "upload_key": resp_json["upload_key"],
                "multipart_upload_id": resp_json["multipart_upload_id"],
                "parts": part_data,
            },
        )


def main():
    # download_input_mp4()
    # create_thumbnail()
    metadata = gather_metadata()
    qualities = determine_qualities(metadata)
    run_transcode(metadata, qualities)
    files_to_upload = generate_manifest(qualities)
    upload_files(files_to_upload)


# Start script
if __name__ == "__main__":
    print(f"Starting processing of {DOWNLOAD_URL}")
    try:
        main()
    except Exception as err:
        print(f"Video Worker failed (attempt #{TASK_ATTEMPT})")
        print(err)
        sys.exit(1)  # Retry Job Task by exiting the process
