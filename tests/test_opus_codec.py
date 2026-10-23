"""Tests for the ESP32 raw-Opus uplink container muxer and decoder."""

from __future__ import annotations

import shutil
import struct
import subprocess

import pytest

from voice_robot.services.opus_codec import (
    RawOpusDecoder16k,
    _crc32_ogg,
    iter_length_prefixed,
    opus_packets_to_ogg,
)


def _frame(packet: bytes) -> bytes:
    return struct.pack(">H", len(packet)) + packet


def test_iter_length_prefixed_recovers_packets() -> None:
    packets = [b"\x01\x02\x03", b"\xaa", b"\xff" * 300]
    stream = b"".join(_frame(p) for p in packets)

    assert list(iter_length_prefixed(stream)) == packets


def test_iter_length_prefixed_ignores_truncated_tail() -> None:
    good = _frame(b"\x01\x02")
    # Length says 5 bytes but only 2 are present -> dropped, not raised.
    truncated = struct.pack(">H", 5) + b"\x01\x02"

    assert list(iter_length_prefixed(good + truncated)) == [b"\x01\x02"]


def test_iter_length_prefixed_empty() -> None:
    assert list(iter_length_prefixed(b"")) == []
    assert list(iter_length_prefixed(b"\x00\x00")) == []  # zero-length record stops


def test_ogg_container_has_opus_headers_and_one_page_per_packet() -> None:
    packets = [b"\xfc\x01\x02", b"\xfc\x03\x04", b"\xfc\x05\x06"]
    ogg = opus_packets_to_ogg(packets)

    assert ogg[:4] == b"OggS"
    assert b"OpusHead" in ogg
    assert b"OpusTags" in ogg
    # BOS + tags + one page per packet.
    assert ogg.count(b"OggS") == 2 + len(packets)


def test_ogg_pages_carry_valid_crc() -> None:
    ogg = opus_packets_to_ogg([b"\xfc\x01\x02"])

    # Walk each page, re-zero the CRC field, recompute, and compare.
    offset = 0
    pages = 0
    while offset < len(ogg):
        assert ogg[offset : offset + 4] == b"OggS"
        num_segments = ogg[offset + 26]
        seg_table = ogg[offset + 27 : offset + 27 + num_segments]
        body_len = sum(seg_table)
        page_len = 27 + num_segments + body_len
        page = bytearray(ogg[offset : offset + page_len])
        stored_crc = struct.unpack_from("<I", page, 22)[0]
        page[22:26] = b"\x00\x00\x00\x00"
        assert _crc32_ogg(bytes(page)) == stored_crc
        offset += page_len
        pages += 1
    assert pages == 3  # head + tags + 1 audio


def test_empty_input_decodes_to_empty() -> None:
    assert RawOpusDecoder16k().decode(b"") == b""
    assert RawOpusDecoder16k().decode(struct.pack(">H", 0)) == b""


def _ogg_pages_to_packets(ogg: bytes) -> list[bytes]:
    """Minimal Ogg de-framer used only to extract real packets for the round-trip."""
    packets: list[bytes] = []
    current = bytearray()
    offset = 0
    while offset < len(ogg):
        num_segments = ogg[offset + 26]
        seg_table = ogg[offset + 27 : offset + 27 + num_segments]
        body = ogg[offset + 27 + num_segments : offset + 27 + num_segments + sum(seg_table)]
        bpos = 0
        for seg in seg_table:
            current += body[bpos : bpos + seg]
            bpos += seg
            if seg < 255:
                packets.append(bytes(current))
                current = bytearray()
        offset += 27 + num_segments + sum(seg_table)
    return packets


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_round_trip_real_opus_packets_decode_to_pcm() -> None:
    # Generate ~0.5s of real Opus via ffmpeg, extract raw packets from its Ogg,
    # re-frame as the ESP32 would, then decode through our raw path.
    gen = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
            "-ac", "1", "-ar", "16000",
            "-c:a", "libopus", "-b:a", "16000",
            "-frame_duration", "20",
            "-f", "ogg", "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if gen.returncode != 0:
        pytest.skip(f"ffmpeg cannot encode libopus: {gen.stderr.decode(errors='replace')[:120]}")

    all_packets = _ogg_pages_to_packets(gen.stdout)
    # Drop the OpusHead / OpusTags header packets; keep audio packets only.
    audio_packets = [p for p in all_packets if not p.startswith((b"OpusHead", b"OpusTags"))]
    assert audio_packets, "no audio packets extracted"

    framed = b"".join(_frame(p) for p in audio_packets)
    pcm = RawOpusDecoder16k().decode(framed)

    # ~0.5s of 16kHz/16bit mono PCM is ~16000 bytes; allow generous slack for
    # encoder priming/padding and rounding.
    assert len(pcm) > 8000
    assert len(pcm) % 2 == 0
