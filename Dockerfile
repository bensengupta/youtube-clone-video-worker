FROM google/shaka-packager:latest as packager

# Use the official Python image.
# https://hub.docker.com/_/python
FROM python:3.12-alpine

# Allow statements and log messages to immediately appear in the logs
ENV PYTHONUNBUFFERED True

# Copy local code to the container image.
ENV APP_HOME /app
WORKDIR $APP_HOME
COPY . ./

# copy binaries from builder
COPY --from=packager \
  /usr/bin/packager \
  /usr/bin/mpd_generator \
  /usr/bin

RUN apk add --no-cache ffmpeg libstdc++

# Install production dependencies.
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python3", "main.py"]