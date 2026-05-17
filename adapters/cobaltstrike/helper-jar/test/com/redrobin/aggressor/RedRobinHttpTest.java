package com.redrobin.aggressor;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.*;

class RedRobinHttpTest {

    private HttpServer server;
    private String baseUrl;
    private final AtomicReference<String> lastAuth = new AtomicReference<>();
    private final AtomicReference<String> lastBody = new AtomicReference<>();

    @BeforeEach
    void setUp() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/echo-post", ex -> {
            lastAuth.set(ex.getRequestHeaders().getFirst("Authorization"));
            byte[] reqBody = ex.getRequestBody().readAllBytes();
            lastBody.set(new String(reqBody));
            byte[] resp = "{\"adapter_session_id\":\"ses_TEST\"}".getBytes();
            ex.sendResponseHeaders(200, resp.length);
            try (OutputStream os = ex.getResponseBody()) { os.write(resp); }
        });
        server.createContext("/echo-get", ex -> {
            lastAuth.set(ex.getRequestHeaders().getFirst("Authorization"));
            byte[] resp = "{\"adapter\":\"cs-main\",\"healthy\":true}".getBytes();
            ex.sendResponseHeaders(200, resp.length);
            try (OutputStream os = ex.getResponseBody()) { os.write(resp); }
        });
        server.createContext("/error-401", ex -> {
            byte[] resp = "{\"detail\":\"unauthorized\"}".getBytes();
            ex.sendResponseHeaders(401, resp.length);
            try (OutputStream os = ex.getResponseBody()) { os.write(resp); }
        });
        server.start();
        int port = server.getAddress().getPort();
        baseUrl = "http://127.0.0.1:" + port;
    }

    @AfterEach
    void tearDown() {
        server.stop(0);
    }

    @Test
    void post_setsBearerAndContentType() throws Exception {
        RedRobinHttp http = new RedRobinHttp(baseUrl, "test-token-abc");
        String result = http.post("/echo-post", "{\"manifest\":{}}");
        assertEquals("Bearer test-token-abc", lastAuth.get());
        assertEquals("{\"manifest\":{}}", lastBody.get());
        assertTrue(result.contains("ses_TEST"));
    }

    @Test
    void get_returnsCodeAndBody() throws Exception {
        RedRobinHttp http = new RedRobinHttp(baseUrl, "test-token-abc");
        RedRobinHttp.Response r = http.get("/echo-get");
        assertEquals(200, r.code);
        assertTrue(r.body.contains("healthy"));
        assertEquals("Bearer test-token-abc", lastAuth.get());
    }

    @Test
    void post_throwsHttpExceptionOn401() {
        RedRobinHttp http = new RedRobinHttp(baseUrl, "test-token-abc");
        RedRobinHttp.HttpException ex = assertThrows(
                RedRobinHttp.HttpException.class,
                () -> http.post("/error-401", "{}")
        );
        assertEquals(401, ex.statusCode);
        assertTrue(ex.responseBody.contains("unauthorized"));
    }

    @Test
    void post_throwsOnConnectionRefused() {
        RedRobinHttp http = new RedRobinHttp("http://127.0.0.1:1", "t");
        assertThrows(Exception.class, () -> http.post("/anything", "{}"));
    }

    @Test
    void get_returnsCodeOnHttp4xx() throws Exception {
        RedRobinHttp http = new RedRobinHttp(baseUrl, "test-token-abc");
        RedRobinHttp.Response r = http.get("/error-401");
        assertEquals(401, r.code);
        assertTrue(r.body.contains("unauthorized"));
    }
}
