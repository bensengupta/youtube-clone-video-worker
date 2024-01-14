# Youtube Clone Video Worker

Video worker for [youtube-clone](https://github.com/bensengupta/youtube-clone)

Downloads a video, transcodes it to various resolutions, packages it as
DASH and HLS manifests for streaming, and re-uploads it.

## Requirements

- Docker
- or Python 3 + ffmpeg + shaka-packager

## Usage

```bash
# run in docker
docker run -e VIDEO_ID=<...> --env-file .env --rm -it $(docker build -q .)
# run locally
VIDEO_ID=<...> CALLBACK_URL=<...> python3 main.py
```
