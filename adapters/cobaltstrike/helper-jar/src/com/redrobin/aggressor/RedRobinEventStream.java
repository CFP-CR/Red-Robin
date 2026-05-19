package com.redrobin.aggressor;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.time.Duration;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import org.json.JSONObject;

/**
 * WebSocket subscriber that streams opsec.verdict events from the daemon's
 * /v1/adapters/events endpoint into a bounded queue Sleep can poll.
 *
 * Mirrors redrobin/tui/ws_client.py semantics:
 *   - auto-reconnect with exponential backoff (1s -> 2s -> ... -> max 60s)
 *   - per-frame JSON decode (org.json)
 *   - drop-oldest backpressure (BlockingQueue capacity 256)
 *
 * The Sleep .cna polls this via pollEvent(timeoutMs). All connection
 * lifecycle, JSON parsing, and reconnect logic lives here so the Sleep
 * code stays minimal and resilient to Sleep fork-scope quirks documented
 * in Task #110.
 */
public class RedRobinEventStream {

    private static final int QUEUE_CAPACITY = 256;
    private static final long BACKOFF_INITIAL_MS = 1_000L;
    private static final long BACKOFF_MAX_MS = 60_000L;

    private final String wsUrl;
    private final HttpClient client;
    private final BlockingQueue<VerdictEvent> queue = new ArrayBlockingQueue<>(QUEUE_CAPACITY);
    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "redrobin-event-stream");
        t.setDaemon(true);
        return t;
    });

    private volatile WebSocket ws;
    private volatile boolean shutdown;
    private long currentBackoffMs = BACKOFF_INITIAL_MS;
    private long lastDisconnectAt;

    public RedRobinEventStream(String httpBaseUrl, String bearerToken) {
        String wsBase = httpBaseUrl.replaceFirst("^http", "ws");
        this.wsUrl = wsBase + "/v1/adapters/events?token=" + bearerToken;
        this.client = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofSeconds(5))
                .build();
        connect();
    }

    /** Blocking poll. Returns null on timeout. Called by Sleep. */
    public VerdictEvent pollEvent(long timeoutMs) throws InterruptedException {
        return queue.poll(timeoutMs, TimeUnit.MILLISECONDS);
    }

    public void close() {
        shutdown = true;
        if (ws != null) {
            try { ws.sendClose(WebSocket.NORMAL_CLOSURE, "shutdown").join(); }
            catch (Exception ignored) {}
        }
        scheduler.shutdownNow();
    }

    private void connect() {
        if (shutdown) return;
        try {
            this.ws = client.newWebSocketBuilder()
                    .buildAsync(URI.create(wsUrl), new Listener())
                    .get(5, TimeUnit.SECONDS);
            currentBackoffMs = BACKOFF_INITIAL_MS;
        } catch (Exception e) {
            scheduleReconnect();
        }
    }

    private void scheduleReconnect() {
        if (shutdown) return;
        long delay = currentBackoffMs;
        currentBackoffMs = Math.min(currentBackoffMs * 2, BACKOFF_MAX_MS);
        if (lastDisconnectAt > 0) {
            long gapMs = System.currentTimeMillis() - lastDisconnectAt;
            if (gapMs > 5_000) {
                System.err.println("[RedRobinEventStream] reconnected after "
                        + gapMs + "ms — verdicts during gap may have been "
                        + "missed; check episodic.db");
            }
        }
        scheduler.schedule(this::connect, delay, TimeUnit.MILLISECONDS);
    }

    private class Listener implements WebSocket.Listener {
        private final StringBuilder buf = new StringBuilder();

        @Override
        public CompletionStage<?> onText(WebSocket ws, CharSequence data, boolean last) {
            buf.append(data);
            if (last) {
                String frame = buf.toString();
                buf.setLength(0);
                VerdictEvent evt = parseVerdictEvent(frame);
                if (evt != null) {
                    if (!queue.offer(evt)) {
                        queue.poll();           // drop oldest
                        queue.offer(evt);
                    }
                }
            }
            ws.request(1);
            return null;
        }

        @Override
        public void onError(WebSocket ws, Throwable error) {
            lastDisconnectAt = System.currentTimeMillis();
            scheduleReconnect();
        }

        @Override
        public CompletionStage<?> onClose(WebSocket ws, int statusCode, String reason) {
            lastDisconnectAt = System.currentTimeMillis();
            scheduleReconnect();
            return null;
        }
    }

    /**
     * Parse a WS frame into a VerdictEvent.
     *
     * Returns null if:
     *   - frame is not valid JSON
     *   - frame's "type" is not "opsec.verdict"
     *
     * Visible for testing; called internally by Listener.onText.
     */
    static VerdictEvent parseVerdictEvent(String frame) {
        try {
            JSONObject root = new JSONObject(frame);
            String type = root.optString("type");
            if (!"opsec.verdict".equals(type)) return null;
            JSONObject p = root.getJSONObject("payload");
            return new VerdictEvent(
                    type,
                    p.optString("source", ""),
                    p.optString("beacon_id", ""),
                    p.optString("verdict", ""),
                    p.optString("reason", ""),
                    p.optString("correlation_id", ""),
                    p.optString("previous_verdict", "")
            );
        } catch (Exception e) {
            return null;
        }
    }
}
