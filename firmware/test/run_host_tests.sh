#!/usr/bin/env bash
# Compile + run all firmware host C unit tests (no ESP32 required).
# These cover pure logic only (state machines, VAD, audio playback, conversation).
# Usage: firmware/test/run_host_tests.sh   [CC=clang]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MAIN="$ROOT/firmware/main"
TESTDIR="$ROOT/firmware/test"
TMP="$(mktemp -d)"
CC="${CC:-cc}"
trap 'rm -rf "$TMP"' EXIT
fail=0

run() {  # <name> <impl.c...>
    local name="$1"; shift
    local impls=()
    local s
    for s in "$@"; do impls+=("$MAIN/$s"); done
    if "$CC" -std=c11 -I "$MAIN" "$TESTDIR/test_${name}.c" "${impls[@]}" -lm \
            -o "$TMP/test_${name}" 2>"$TMP/err_${name}"; then
        if ! "$TMP/test_${name}"; then
            echo "RUN FAIL test_${name}"; fail=1
        fi
    else
        echo "BUILD FAIL test_${name}:"; cat "$TMP/err_${name}"; fail=1
    fi
}

run vad vad.c
run ui_state ui_state.c
run audio_playback audio_playback.c
run ble_gateway ble_gateway.c
run conversation conversation.c vad.c ui_state.c

if [ "$fail" -ne 0 ]; then
    echo "firmware host tests FAILED"
    exit 1
fi
echo "all firmware host tests passed"
