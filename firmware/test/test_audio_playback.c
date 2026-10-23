#include <assert.h>
#include <stdint.h>
#include <stdio.h>

#include "audio_playback.h"

static size_t fake_written;
static int fake_write_calls;

static int fake_sink(const uint8_t *data, size_t len, void *ctx) {
    (void)ctx;
    assert(data != NULL);
    fake_written += len;
    fake_write_calls++;
    return (int)len;
}

static void start_write_end_tracks_bytes(void) {
    voice_audio_playback_t playback;
    voice_audio_playback_init(&playback, fake_sink, NULL);

    assert(!voice_audio_playback_is_active(&playback));

    voice_audio_playback_start(&playback);
    assert(voice_audio_playback_is_active(&playback));

    uint8_t chunk_a[4] = {1, 2, 3, 4};
    uint8_t chunk_b[2] = {5, 6};
    assert(voice_audio_playback_write(&playback, chunk_a, sizeof(chunk_a)) == 4);
    assert(voice_audio_playback_write(&playback, chunk_b, sizeof(chunk_b)) == 2);
    assert(voice_audio_playback_bytes_written(&playback) == 6);
    assert(fake_written == 6);
    assert(fake_write_calls == 2);

    voice_audio_playback_end(&playback);
    assert(!voice_audio_playback_is_active(&playback));
    assert(voice_audio_playback_bytes_written(&playback) == 6);
}

static void dropped_when_inactive_or_interrupted(void) {
    voice_audio_playback_t playback;
    voice_audio_playback_init(&playback, fake_sink, NULL);

    uint8_t chunk[3] = {1, 2, 3};
    assert(voice_audio_playback_write(&playback, chunk, sizeof(chunk)) == 0);

    voice_audio_playback_start(&playback);
    assert(voice_audio_playback_write(&playback, chunk, sizeof(chunk)) == 3);
    voice_audio_playback_interrupt(&playback);

    assert(!voice_audio_playback_is_active(&playback));
    assert(voice_audio_playback_write(&playback, chunk, sizeof(chunk)) == 0);
    assert(voice_audio_playback_bytes_written(&playback) == 3);
}

static void queue_buffers_chunks_and_reports_dropped_bytes(void) {
    uint8_t storage[8];
    voice_audio_playback_queue_t queue;
    voice_audio_playback_queue_init(&queue, storage, sizeof(storage));

    uint8_t chunk_a[3] = {1, 2, 3};
    uint8_t chunk_b[4] = {4, 5, 6, 7};
    assert(voice_audio_playback_queue_push(&queue, chunk_a, sizeof(chunk_a)) == 3);
    assert(voice_audio_playback_queue_push(&queue, chunk_b, sizeof(chunk_b)) == 4);
    assert(voice_audio_playback_queue_size(&queue) == 7);
    assert(voice_audio_playback_queue_dropped_bytes(&queue) == 0);

    uint8_t out[5] = {0};
    assert(voice_audio_playback_queue_pop(&queue, out, sizeof(out)) == 5);
    assert(out[0] == 1);
    assert(out[1] == 2);
    assert(out[2] == 3);
    assert(out[3] == 4);
    assert(out[4] == 5);
    assert(voice_audio_playback_queue_size(&queue) == 2);

    uint8_t chunk_c[7] = {8, 9, 10, 11, 12, 13, 14};
    assert(voice_audio_playback_queue_push(&queue, chunk_c, sizeof(chunk_c)) == 6);
    assert(voice_audio_playback_queue_size(&queue) == 8);
    assert(voice_audio_playback_queue_dropped_bytes(&queue) == 1);

    uint8_t rest[8] = {0};
    assert(voice_audio_playback_queue_pop(&queue, rest, sizeof(rest)) == 8);
    assert(rest[0] == 6);
    assert(rest[1] == 7);
    assert(rest[2] == 8);
    assert(rest[7] == 13);
    assert(voice_audio_playback_queue_size(&queue) == 0);
}

static void queue_clear_discards_pending_audio(void) {
    uint8_t storage[4];
    voice_audio_playback_queue_t queue;
    voice_audio_playback_queue_init(&queue, storage, sizeof(storage));

    uint8_t chunk[4] = {1, 2, 3, 4};
    assert(voice_audio_playback_queue_push(&queue, chunk, sizeof(chunk)) == 4);
    voice_audio_playback_queue_clear(&queue);

    uint8_t out[4] = {0};
    assert(voice_audio_playback_queue_size(&queue) == 0);
    assert(voice_audio_playback_queue_pop(&queue, out, sizeof(out)) == 0);
}

static void queue_reset_clears_pending_audio_and_drop_stats(void) {
    uint8_t storage[2];
    voice_audio_playback_queue_t queue;
    voice_audio_playback_queue_init(&queue, storage, sizeof(storage));

    uint8_t chunk[3] = {1, 2, 3};
    assert(voice_audio_playback_queue_push(&queue, chunk, sizeof(chunk)) == 2);
    assert(voice_audio_playback_queue_dropped_bytes(&queue) == 1);

    voice_audio_playback_queue_reset(&queue);
    assert(voice_audio_playback_queue_size(&queue) == 0);
    assert(voice_audio_playback_queue_dropped_bytes(&queue) == 0);
}

int main(void) {
    start_write_end_tracks_bytes();
    fake_written = 0;
    fake_write_calls = 0;
    dropped_when_inactive_or_interrupted();
    queue_buffers_chunks_and_reports_dropped_bytes();
    queue_clear_discards_pending_audio();
    queue_reset_clears_pending_audio_and_drop_stats();
    puts("audio_playback tests passed");
    return 0;
}
