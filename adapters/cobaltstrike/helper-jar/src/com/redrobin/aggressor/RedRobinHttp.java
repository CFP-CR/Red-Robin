package com.redrobin.aggressor;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

/**
 * HTTP helper for the Red Robin Cobalt Strike Aggressor script.
 *
 * Wraps java.net.http.HttpClient so the Sleep-side .cna never reflects on
 * jdk.internal.net.http impl classes. Sleep reflection targets only
 * methods on this public class — declaringClass is
 * com.redrobin.aggressor.RedRobinHttp, in the unnamed module loaded by
 * URLClassLoader (same module as the Sleep caller), so the JVM access
 * check passes trivially.
 */
public class RedRobinHttp {

    private final String baseUrl;
    private final String bearerToken;
    private final HttpClient client;

    public RedRobinHttp(String baseUrl, String bearerToken) {
        this.baseUrl = baseUrl;
        this.bearerToken = bearerToken;
        this.client = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofMillis(200))
                .build();
    }

    public String post(String path, String body) throws Exception {
        HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + path))
                .timeout(Duration.ofMillis(200))
                .header("Authorization", "Bearer " + bearerToken)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();
        HttpResponse<String> resp = client.send(req, HttpResponse.BodyHandlers.ofString());
        if (resp.statusCode() >= 400) {
            throw new HttpException(resp.statusCode(), resp.body());
        }
        return resp.body();
    }

    public Response get(String path) throws Exception {
        HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + path))
                .timeout(Duration.ofMillis(1000))
                .header("Authorization", "Bearer " + bearerToken)
                .GET()
                .build();
        HttpResponse<String> resp = client.send(req, HttpResponse.BodyHandlers.ofString());
        return new Response(resp.statusCode(), resp.body());
    }

    // ULID generator — used to produce wire envelope `id` values that
    // match the v0.1 schema regex ^evt_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$.
    // 48-bit ms timestamp prefix + 80-bit random suffix, Crockford-b32
    // encoded. Spec: https://github.com/ulid/spec.

    private static final char[] CROCKFORD_B32 = {
        '0','1','2','3','4','5','6','7','8','9',
        'A','B','C','D','E','F','G','H','J','K',
        'M','N','P','Q','R','S','T','V','W','X','Y','Z'
    };

    private static final java.security.SecureRandom ULID_RNG = new java.security.SecureRandom();

    public String ulid() {
        long ts = System.currentTimeMillis();
        byte[] randomness = new byte[10];  // 80 bits
        ULID_RNG.nextBytes(randomness);

        char[] out = new char[26];
        // 10 chars for the 48-bit timestamp prefix (big-endian)
        for (int i = 9; i >= 0; i--) {
            out[i] = CROCKFORD_B32[(int) (ts & 0x1f)];
            ts >>>= 5;
        }
        // 16 chars for the 80-bit random tail
        int bitBuf = 0;
        int bitsHeld = 0;
        int outIdx = 10;
        for (byte b : randomness) {
            bitBuf = (bitBuf << 8) | (b & 0xff);
            bitsHeld += 8;
            while (bitsHeld >= 5) {
                bitsHeld -= 5;
                out[outIdx++] = CROCKFORD_B32[(bitBuf >>> bitsHeld) & 0x1f];
            }
        }
        // 80 bits / 5 bits-per-char = 16 chars exactly, no leftover bits
        // 10 (ts) + 16 (random) = 26 chars
        return new String(out);
    }

    public static class Response {
        public final int code;
        public final String body;

        public Response(int code, String body) {
            this.code = code;
            this.body = body;
        }
    }

    public static class HttpException extends RuntimeException {
        public final int statusCode;
        public final String responseBody;

        public HttpException(int statusCode, String responseBody) {
            super("HTTP " + statusCode + ": " + responseBody);
            this.statusCode = statusCode;
            this.responseBody = responseBody;
        }
    }
}
