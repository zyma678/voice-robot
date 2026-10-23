# Voice Robot backend image (run-from-source: WORKDIR is the project root so the
# server can locate ./web_console via Path(__file__).parent.parent, exactly like
# local dev). Build context is the repo root (see docker-compose.yml).
FROM python:3.11-slim

WORKDIR /app

# Deps only (pinned in deploy/requirements.txt, mirrors pyproject [project].dependencies).
COPY deploy/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Source (web_console is served from the filesystem, not installed as a package).
COPY voice_robot ./voice_robot
COPY web_console ./web_console
# Character trace dataset (MakeMeAHanzi graphics.txt).
COPY voice_robot/resources ./voice_robot/resources

# runtime/ (memory + console config) is a mounted volume; created on first write.
EXPOSE 8090

# No --reload in prod; single worker keeps the in-process device registry / ws
# state coherent (the app holds connection state in module globals).
# --ws-ping-timeout 180: hardware is too busy to answer uvicorn's WS keepalive
# ping during sustained TTS-then-record bursts; the default 20s timeout drops
# the connection mid-turn. The app's own 15s heartbeat covers liveness.
CMD ["uvicorn", "voice_robot.server:app", "--host", "0.0.0.0", "--port", "8090", \
     "--ws-ping-interval", "25", "--ws-ping-timeout", "180"]
