from __future__ import annotations

from dataclasses import dataclass
import struct
import shutil
import subprocess


class OpusCodecUnavailable(RuntimeError):
    pass


# Opus always runs its internal clock at 48 kHz, so granule positions and
# per-frame sample counts in the Ogg container are expressed at 48 kHz
# regardless of the 16 kHz capture rate. A 20 ms frame is 960 samples @ 48 kHz.
_OPUS_GRANULE_RATE_HZ = 48000
_FRAME_MS = 20
_SAMPLES_PER_FRAME_48K = _OPUS_GRANULE_RATE_HZ * _FRAME_MS // 1000  # 960

_OGG_CAPTURE = b"OggS"
_CRC_POLY = 0x04C11DB7


def iter_length_prefixed(buffer: bytes):
    """
    Yield raw Opus packets from a stream of ``[u16be length][payload]`` records.

    This is the on-wire framing the ESP32 uses for ``opus_raw`` uplink: each
    20 ms Opus packet is prefixed with its big-endian length so the server can
    recover packet boundaries after the websocket concatenates binary frames.
    A trailing partial record (truncated stream) is ignored.
    """
    offset = 0
    total = len(buffer)
    while offset + 2 <= total:
        (length,) = struct.unpack_from(">H", buffer, offset)
        offset += 2
        if length == 0 or offset + length > total:
            break
        yield buffer[offset : offset + length]
        offset += length


def _crc32_ogg(data: bytes) -> int:
    """Ogg's CRC: poly 0x04c11db7, init 0, no input/output reflection, no xorout."""
    crc = 0
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            if crc & 0x80000000:
                crc = ((crc << 1) ^ _CRC_POLY) & 0xFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFF
    return crc


def _ogg_page(header_type: int, granule: int, serial: int, seq: int, packets: list[bytes]) -> bytes:
    """Build one Ogg page carrying whole packets (no continued packets)."""
    segments = bytearray()
    body = bytearray()
    for packet in packets:
        remaining = len(packet)
        while True:
            seg = min(remaining, 255)
            segments.append(seg)
            remaining -= seg
            if seg < 255:
                break
        body += packet
    header = bytearray()
    header += _OGG_CAPTURE
    header += bytes([0])  # stream structure version
    header += bytes([header_type])
    header += struct.pack("<q", granule)
    header += struct.pack("<I", serial)
    header += struct.pack("<I", seq)
    header += struct.pack("<I", 0)  # CRC placeholder
    header += bytes([len(segments)])
    header += bytes(segments)
    page = bytes(header) + bytes(body)
    crc = _crc32_ogg(page)
    return page[:22] + struct.pack("<I", crc) + page[26:]


def opus_packets_to_ogg(packets: list[bytes], *, serial: int = 1) -> bytes:
    """
    Wrap raw Opus packets into a minimal Ogg/Opus stream ffmpeg can decode.

    Emits the mandatory OpusHead (BOS) and OpusTags header pages, then one
    audio page per packet with a 48 kHz granule position. pre_skip is 0 so no
    real audio is trimmed at the start (a few ms of encoder priming is harmless
    for ASR). Mono, 16 kHz input sample rate.
    """
    if not packets:
        return b""

    opus_head = (
        b"OpusHead"
        + bytes([1])  # version
        + bytes([1])  # channel count
        + struct.pack("<H", 0)  # pre-skip
        + struct.pack("<I", 16000)  # input sample rate
        + struct.pack("<h", 0)  # output gain
        + bytes([0])  # channel mapping family
    )
    opus_tags = (
        b"OpusTags"
        + struct.pack("<I", len(b"voice-robot"))
        + b"voice-robot"
        + struct.pack("<I", 0)  # user comment count
    )

    out = bytearray()
    out += _ogg_page(0x02, 0, serial, 0, [opus_head])  # BOS
    out += _ogg_page(0x00, 0, serial, 1, [opus_tags])
    granule = 0
    seq = 2
    for packet in packets:
        granule += _SAMPLES_PER_FRAME_48K
        out += _ogg_page(0x00, granule, serial, seq, [packet])
        seq += 1
    return bytes(out)


@dataclass(frozen=True)
class OpusDecoder16k:
    """
    Decode Opus -> PCM s16le 16kHz mono.

    Current implementation shells out to `ffmpeg` and expects the input bytes to
    be a container format that ffmpeg understands (e.g. WebM/Opus, Ogg/Opus).
    This matches the Web console path using MediaRecorder. The ESP32 path may
    later switch to raw Opus frames once we add a proper packet decoder.
    """

    def decode(self, opus_bytes: bytes) -> bytes:
        if not opus_bytes:
            return b""
        if shutil.which("ffmpeg") is None:
            raise OpusCodecUnavailable("ffmpeg not found; cannot decode opus")

        # Decode to raw PCM s16le 16k mono.
        # -i pipe:0 supports common containers (webm/ogg) emitted by MediaRecorder.
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "s16le",
                "pipe:1",
            ],
            input=opus_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", errors="replace").strip()
            raise OpusCodecUnavailable(f"ffmpeg opus decode failed: {detail or proc.returncode}")
        return proc.stdout


@dataclass(frozen=True)
class RawOpusDecoder16k:
    """
    Decode the ESP32 ``opus_raw`` uplink -> PCM s16le 16kHz mono.

    Input is the length-prefixed raw-Opus byte stream (``[u16be len][packet]``
    records). Packets are wrapped into an in-memory Ogg/Opus container in pure
    Python, then handed to the existing ffmpeg container decoder. This keeps the
    backend free of native Opus bindings while supporting raw device frames.
    """

    def decode(self, framed_bytes: bytes) -> bytes:
        if not framed_bytes:
            return b""
        packets = list(iter_length_prefixed(framed_bytes))
        if not packets:
            return b""
        ogg = opus_packets_to_ogg(packets)
        return OpusDecoder16k().decode(ogg)
