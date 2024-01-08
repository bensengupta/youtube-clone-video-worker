import json
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
        self, name: str, width: int, height: int, filename: str, ffmpeg_flags: str
    ):
        self.name = name
        self.width = width
        self.height = height
        self.filename = filename
        self.ffmpeg_flags = ffmpeg_flags


def download_input_mp4():
    print("\n===== Downloading input =====")

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


def determine_qualities(metadata: VideoMetadata) -> list[VideoQuality]:
    print("\n===== Determining qualities =====")

    qualities = [
        # 1080p
        VideoQuality(
            name="1080p",
            width=1920,
            height=1080,
            filename="1080p.mp4",
            ffmpeg_flags="-vf scale=1920:1080 -b:a 192k",
        ),
        # 720p
        VideoQuality(
            name="720p",
            width=1280,
            height=720,
            filename="720p.mp4",
            ffmpeg_flags="-vf scale=1280:720 -b:a 128k",
        ),
        # 480p (minimum resolution)
        VideoQuality(
            name="480p",
            width=0,
            height=0,
            filename="480p.mp4",
            ffmpeg_flags="-vf scale=854:480 -b:a 96k",
        ),
    ]

    def filter_keep_lower_resolutions(q: VideoQuality):
        return q.width <= metadata.width or q.height <= metadata.height

    qualities = list(filter(filter_keep_lower_resolutions, qualities))

    return qualities


def run_transcode(metadata: VideoMetadata, qualities: list[VideoQuality]):
    print("\n===== Running transcode =====")

    common_params = [
        "-c:v h264",  # video codec H.264
        "-profile:v main",  # H.264 profile main
        "-level:v 4.0",  # H.264 level 4.0
        "-c:a aac",  # audio codec AAC
        "-crf 22",  # constant rate factor, lower is better quality
        f"-r {min(metadata.frame_rate, 60)}",  # keep framerate (max 60fps)
        "-pix_fmt yuv420p",
        "-movflags +faststart",
    ]

    def map_to_subcommand(q: VideoQuality):
        return " ".join([q.ffmpeg_flags, *common_params, q.filename])

    subcommands = list(map(map_to_subcommand, qualities))

    command = f"./ffmpeg/ffmpeg -i input.mp4 {' '.join(subcommands)}"

    print(command)

    os.system(command)


def generate_manifest(qualities: list[VideoQuality]):
    print("\n===== Generating manifest =====")

    def map_to_dash_stream(q: VideoQuality):
        return (
            f"input={q.filename},stream=audio,output=audio_{q.name}_dash.mp4,playlist_name=audio_{q.name}.m3u8,hls_group_id=audio,hls_name=ENGLISH "
            + f"input={q.filename},stream=video,output=video_{q.name}_dash.mp4,playlist_name=video_{q.name}.m3u8,iframe_playlist_name=video_{q.name}_iframe.m3u8"
        )

    subcommands = list(map(map_to_dash_stream, qualities))

    command_parts = [
        "./shaka-packager/packager",
        *subcommands,
        "--hls_master_playlist_output hls_manifest.m3u8",
        "--mpd_output dash_manifest.mpd",
    ]

    command = " ".join(command_parts)

    print(command)

    os.system(command)


def get_file_size(filename: str):
    return os.path.getsize(filename)


class CallbackPostResponse(TypedDict):
    multipart_upload_id: str
    upload_key: str
    presigned_urls: list[str]
    part_size: int


def upload_files(qualities: list[VideoQuality]):
    print("\n===== Uploading output =====")

    print(list(map(lambda q: q.filename, qualities)))

    for quality in qualities:
        req_json = {
            "quality_name": quality.name,
            "content_type": "video/mp4",
            "file_size": get_file_size(quality.filename),
        }

        print("Requesting upload of", quality.filename, ":", req_json)

        resp = requests.post(f"{CALLBACK_URL}/request-quality-upload", json=req_json)
        resp_json: CallbackPostResponse = resp.json()

        part_data = []

        with open(quality.filename, "rb") as f:
            print("Reading", quality.filename)
            for idx, presigned_url in enumerate(resp_json["presigned_urls"]):
                print("Uploading part", idx + 1, "of", quality.filename)
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
            f"{CALLBACK_URL}/finish-quality-upload",
            json={
                "upload_key": resp_json["upload_key"],
                "multipart_upload_id": resp_json["multipart_upload_id"],
                "parts": part_data,
            },
        )

    # resp = requests.post(CALLBACK_URL, files=files)


def main():
    # download_input_mp4()
    metadata = gather_metadata()
    qualities = determine_qualities(metadata)
    # run_transcode(metadata, qualities)
    generate_manifest(qualities)
    # upload_files(qualities)


# Start script
if __name__ == "__main__":
    print(f"Starting processing of {DOWNLOAD_URL}")
    try:
        main()
    except Exception as err:
        print(f"Video Worker failed (attempt #{TASK_ATTEMPT})")
        print(err)
        sys.exit(1)  # Retry Job Task by exiting the process
