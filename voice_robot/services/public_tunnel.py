import atexit
import queue
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from urllib import error, request


_TRYCLOUDFLARE_URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


@dataclass(frozen=True)
class PublicTunnelConfig:
    command: str = "cloudflared"
    target_url: str = "http://127.0.0.1:8090"
    timeout_seconds: float = 20.0
    # After cloudflared prints the URL the trycloudflare edge still needs a few
    # seconds before it can actually route traffic. Returning the URL too early
    # makes external fetchers (e.g. Volc AUC pulling the audio file) hit the
    # tunnel before it is reachable. Probe the URL until it responds first.
    readiness_timeout_seconds: float = 25.0
    readiness_probe_interval_seconds: float = 1.0


class CloudflaredTunnelManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._public_url = ""

    def ensure_public_url(self, config: PublicTunnelConfig) -> str:
        with self._lock:
            if self._public_url and self._process and self._process.poll() is None:
                return self._public_url
            self._terminate_locked()
            self._public_url = self._start_locked(config)
            return self._public_url

    def close(self) -> None:
        with self._lock:
            self._terminate_locked()

    def _terminate_locked(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None

    def _start_locked(self, config: PublicTunnelConfig) -> str:
        command_path = shutil.which(config.command)
        if not command_path:
            raise RuntimeError(
                "cloudflared is not installed; install it and retry, or set ASR audio url base manually"
            )

        proc = subprocess.Popen(
            [
                command_path,
                "tunnel",
                "--url",
                config.target_url,
                "--no-autoupdate",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._process = proc

        lines: queue.Queue[str] = queue.Queue()
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                thread = threading.Thread(
                    target=_drain_stream,
                    args=(stream, lines),
                    daemon=True,
                )
                thread.start()

        deadline = time.monotonic() + max(config.timeout_seconds, 1)
        recent_lines: list[str] = []
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            try:
                line = lines.get(timeout=0.25)
            except queue.Empty:
                continue
            recent_lines.append(line.strip())
            match = _TRYCLOUDFLARE_URL_RE.search(line)
            if match:
                public_url = match.group(0).rstrip("/")
                _wait_until_reachable(
                    public_url,
                    timeout_seconds=config.readiness_timeout_seconds,
                    interval_seconds=config.readiness_probe_interval_seconds,
                )
                return public_url

        self._terminate_locked()
        detail = " | ".join(item for item in recent_lines[-5:] if item)
        raise RuntimeError(
            "cloudflared tunnel did not become ready"
            + (f": {detail}" if detail else "")
        )


def _drain_stream(stream, lines: queue.Queue[str]) -> None:
    try:
        for line in stream:
            lines.put(line)
    except Exception:
        return


def _wait_until_reachable(
    public_url: str,
    *,
    timeout_seconds: float,
    interval_seconds: float,
) -> None:
    """Block until the freshly created tunnel edge actually serves traffic.

    cloudflared prints the public URL before the trycloudflare edge can route
    requests. Probing the URL ourselves avoids returning a URL that external
    fetchers (e.g. Volc AUC) cannot yet reach, which surfaced as
    "audio download failed" errors.
    """
    deadline = time.monotonic() + max(timeout_seconds, 0)
    while True:
        try:
            req = request.Request(public_url, method="HEAD")
            with request.urlopen(req, timeout=5):
                return
        except error.HTTPError:
            # Any HTTP response means the edge is routing to our server.
            return
        except (error.URLError, OSError):
            pass
        if time.monotonic() >= deadline:
            return
        time.sleep(max(interval_seconds, 0.1))


default_tunnel_manager = CloudflaredTunnelManager()
atexit.register(default_tunnel_manager.close)
