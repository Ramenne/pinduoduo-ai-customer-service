package com.feifei.phonebridge;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

final class HttpBridgeServer {
    private static final int MAX_HEADER_COUNT = 64;
    private static final int MAX_HEADER_BYTES = 32 * 1024;
    private final Context context;
    private final ActionDispatcher dispatcher;
    private final ExecutorService workers = new ThreadPoolExecutor(
            2,
            4,
            30L,
            TimeUnit.SECONDS,
            new ArrayBlockingQueue<>(16),
            new ThreadPoolExecutor.AbortPolicy()
    );
    private volatile boolean running;
    private volatile ServerSocket serverSocket;
    private Thread acceptThread;

    HttpBridgeServer(Context context) {
        this.context = context.getApplicationContext();
        this.dispatcher = new ActionDispatcher(context);
    }

    synchronized void start() {
        if (running) {
            return;
        }
        running = true;
        acceptThread = new Thread(this::acceptLoop, "phonebridge-http");
        acceptThread.start();
    }

    synchronized void stop() {
        running = false;
        if (serverSocket != null) {
            try {
                serverSocket.close();
            } catch (IOException ignored) {
            }
        }
        workers.shutdownNow();
    }

    private void acceptLoop() {
        // Release 默认不启动此诊断桥；启用时绑定地址由受审构建配置固定。
        // 0.0.0.0 仅用于 debug 或显式确认过暴露风险的诊断构建。
        try (ServerSocket socket = new ServerSocket(
                BridgeState.PORT,
                20,
                InetAddress.getByName(BuildConfig.LOCAL_DIAGNOSTIC_BIND_ADDRESS))) {
            serverSocket = socket;
            while (running) {
                Socket client = socket.accept();
                client.setSoTimeout(10000);
                try {
                    workers.execute(() -> handle(client));
                } catch (RejectedExecutionException overloaded) {
                    try {
                        client.close();
                    } catch (IOException ignored) {
                    }
                }
            }
        } catch (IOException error) {
            if (running) {
                JSONObject detail = new JSONObject();
                try {
                    detail.put("error", error.toString());
                } catch (Exception ignored) {
                }
                AuditLog.append(context, "server_error", false, detail);
            }
        } finally {
            serverSocket = null;
        }
    }

    private void handle(Socket client) {
        try (Socket socket = client;
             InputStream rawInput = new BufferedInputStream(socket.getInputStream());
             OutputStream output = new BufferedOutputStream(socket.getOutputStream())) {
            Request request = Request.read(rawInput);
            if (request == null) {
                return;
            }
            route(request, output);
        } catch (Exception error) {
            JSONObject detail = new JSONObject();
            try {
                detail.put("error", error.toString());
            } catch (Exception ignored) {
            }
            AuditLog.append(context, "request_error", false, detail);
        } catch (LinkageError error) {
            JSONObject detail = new JSONObject();
            try {
                detail.put("error", error.toString());
            } catch (Exception ignored) {
            }
            AuditLog.append(context, "request_linkage_error", false, detail);
        }
    }

    private void route(Request request, OutputStream output) throws Exception {
        // 所有端点（包括 health）统一鉴权，避免 0.0.0.0 暴露后泄露前台应用和服务状态。
        String suppliedToken = request.headers.getOrDefault("x-phonebridge-token", "");
        if (!secureEquals(BridgeState.ensureToken(context), suppliedToken)) {
            respondJson(output, 401, ActionDispatcher.outcome(false, "unauthorized"));
            return;
        }
        if ("GET".equals(request.method) && "/health".equals(request.path)) {
            respondJson(output, 200, BridgeState.status(context));
            return;
        }

        if ("GET".equals(request.method) && "/observe".equals(request.path)) {
            BridgeAccessibilityService service = BridgeAccessibilityService.getInstance();
            if (service == null) {
                respondJson(output, 503, ActionDispatcher.outcome(false, "accessibility_not_connected"));
            } else {
                respondJson(output, 200, service.observe());
            }
            return;
        }
        // ===== 闲鱼界面识别插件（板块1，oin 新增）=====
        // 只做识别与结构化，不做动作；动作仍走 /action。
        // 输入一律复用底座 observe() 的 UI 树。
        if ("GET".equals(request.method) && "/xianyu/info".equals(request.path)) {
            respondJson(output, 200,
                    com.feifei.phonebridge.plugin.messaging.MessagingPlatformRegistry.describe("xianyu"));
            return;
        }
        if ("GET".equals(request.method) && request.path != null && request.path.startsWith("/xianyu/")) {
            BridgeAccessibilityService service = BridgeAccessibilityService.getInstance();
            if (service == null) {
                respondJson(output, 503, ActionDispatcher.outcome(false, "accessibility_not_connected"));
                return;
            }
            JSONObject observeJson = service.observe();
            JSONObject pluginResult;
            switch (request.path) {
                case "/xianyu/page":
                    pluginResult = com.feifei.phonebridge.plugin.xianyu.XianYuPlugin.page(observeJson);
                    break;
                case "/xianyu/items":
                    pluginResult = com.feifei.phonebridge.plugin.xianyu.XianYuPlugin.items(observeJson);
                    break;
                case "/xianyu/messages":
                    pluginResult = com.feifei.phonebridge.plugin.xianyu.XianYuPlugin.messages(observeJson);
                    break;
                case "/xianyu/snapshot":
                    pluginResult = com.feifei.phonebridge.plugin.messaging.MessagingPlatformRegistry
                            .snapshot("xianyu", observeJson);
                    break;
                default:
                    pluginResult = null;
            }
            if (pluginResult == null) {
                respondJson(output, 404, ActionDispatcher.outcome(false, "not_found"));
            } else {
                respondJson(output, 200, pluginResult);
            }
            return;
        }
        if ("GET".equals(request.method) && "/audit".equals(request.path)) {
            JSONObject json = new JSONObject();
            json.put("entries", AuditLog.recent(context));
            respondJson(output, 200, json);
            return;
        }
        if ("POST".equals(request.method) && "/action".equals(request.path)) {
            JSONObject action = new JSONObject(new String(request.body, StandardCharsets.UTF_8));
            respondJson(output, 200, dispatcher.dispatch(action));
            return;
        }
        respondJson(output, 404, ActionDispatcher.outcome(false, "not_found"));
    }

    private static void respondJson(OutputStream output, int status, JSONObject json) throws IOException {
        respondBytes(output, status, "application/json; charset=utf-8",
                json.toString().getBytes(StandardCharsets.UTF_8));
    }

    private static void respondBytes(OutputStream output, int status, String contentType, byte[] body) throws IOException {
        String reason = status == 200 ? "OK" : status == 401 ? "Unauthorized" :
                status == 404 ? "Not Found" : status == 501 ? "Not Implemented" :
                        status == 503 ? "Service Unavailable" : "Error";
        String headers = "HTTP/1.1 " + status + " " + reason + "\r\n" +
                "Content-Type: " + contentType + "\r\n" +
                "Content-Length: " + body.length + "\r\n" +
                "Cache-Control: no-store\r\n" +
                "Connection: close\r\n\r\n";
        output.write(headers.getBytes(StandardCharsets.US_ASCII));
        output.write(body);
        output.flush();
    }

    private static boolean secureEquals(String expected, String supplied) {
        byte[] expectedBytes = expected == null ? new byte[0] : expected.getBytes(StandardCharsets.UTF_8);
        byte[] suppliedBytes = supplied == null ? new byte[0] : supplied.getBytes(StandardCharsets.UTF_8);
        return MessageDigest.isEqual(expectedBytes, suppliedBytes);
    }

    private static final class Request {
        final String method;
        final String path;
        final Map<String, String> headers;
        final byte[] body;

        Request(String method, String path, Map<String, String> headers, byte[] body) {
            this.method = method;
            this.path = path;
            this.headers = headers;
            this.body = body;
        }

        static Request read(InputStream input) throws IOException {
            String requestLine = readLine(input);
            if (requestLine == null || requestLine.trim().isEmpty()) {
                return null;
            }
            String[] parts = requestLine.split(" ");
            if (parts.length < 2) {
                return null;
            }
            Map<String, String> headers = new HashMap<>();
            String line;
            int headerCount = 0;
            int headerBytes = 0;
            while ((line = readLine(input)) != null && !line.isEmpty()) {
                headerCount++;
                headerBytes += line.length();
                if (headerCount > MAX_HEADER_COUNT || headerBytes > MAX_HEADER_BYTES) {
                    throw new IOException("Request headers too large");
                }
                int separator = line.indexOf(':');
                if (separator > 0) {
                    headers.put(
                            line.substring(0, separator).trim().toLowerCase(Locale.US),
                            line.substring(separator + 1).trim()
                    );
                }
            }
            int contentLength = 0;
            try {
                contentLength = Integer.parseInt(headers.getOrDefault("content-length", "0"));
            } catch (NumberFormatException ignored) {
            }
            if (contentLength < 0 || contentLength > 1024 * 1024) {
                throw new IOException("Invalid content length");
            }
            byte[] body = new byte[contentLength];
            int offset = 0;
            while (offset < contentLength) {
                int read = input.read(body, offset, contentLength - offset);
                if (read == -1) {
                    throw new IOException("Unexpected end of request body");
                }
                offset += read;
            }
            return new Request(parts[0].toUpperCase(Locale.US), parts[1].split("\\?")[0], headers, body);
        }

        private static String readLine(InputStream input) throws IOException {
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            int value;
            boolean seen = false;
            while ((value = input.read()) != -1) {
                seen = true;
                if (value == '\n') {
                    break;
                }
                if (value != '\r') {
                    bytes.write(value);
                }
                if (bytes.size() > 8192) {
                    throw new IOException("Header line too long");
                }
            }
            if (!seen && bytes.size() == 0) {
                return null;
            }
            return bytes.toString(StandardCharsets.US_ASCII.name());
        }
    }
}
