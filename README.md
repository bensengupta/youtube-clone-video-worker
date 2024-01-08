# Youtube Clone Video Worker

Video worker for [youtube-clone](https://github.com/bensengupta/youtube-clone)

Downloads a video, transcodes it to various resolutions, packages it as
DASH and HLS manifests for streaming, and re-uploads it.

## Requirements

- Python 3

## Usage

```
DOWNLOAD_URL=<...> CALLBACK_URL=<...> python3 main.py
```

## Dependency updates

### Update ffmpeg

```bash
# Sourced from https://ffmpeg.org/download.html

wget https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
tar xf ffmpeg-release-amd64-static.tar.xz
mv ffmpeg-*-amd64-static ffmpeg
rm ffmpeg-release-amd64-static.tar.xz
```

### Update shaka-packager

```bash
# Sourced from https://github.com/shaka-project/shaka-packager/releases

# Replace RELEASE_VERSION with desired version
RELEASE_VERSION=v2.6.1
wget https://github.com/shaka-project/shaka-packager/releases/download/$RELEASE_VERSION/packager-linux-x64
mv packager-linux-x64 shaka-packager/packager
```
