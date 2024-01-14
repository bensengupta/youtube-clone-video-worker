# Use the official Python image.
# https://hub.docker.com/_/python
FROM python:3.12-slim

# Allow statements and log messages to immediately appear in the logs
ENV PYTHONUNBUFFERED True

# Copy local code to the container image.
ENV APP_HOME /app
WORKDIR $APP_HOME
COPY . ./

# install ffmpeg and ffprobe
RUN apt-get install -y ffmpeg ffprobe

# Install production dependencies.
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python3", "main.py"]