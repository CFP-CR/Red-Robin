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
