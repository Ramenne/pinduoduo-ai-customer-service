package com.feifei.phonebridge;

import java.net.ConnectException;
import java.net.ProtocolException;
import java.net.SocketTimeoutException;
import java.net.UnknownHostException;

import javax.net.ssl.SSLException;

final class TransportFailureClassifier {
    private TransportFailureClassifier() {
    }

    static String classify(Throwable failure, int httpStatus) {
        if (httpStatus >= 300 && httpStatus < 400) {
            return "http_redirect";
        }
        if (failure instanceof UnknownHostException) {
            return "dns";
        }
        if (failure instanceof SSLException) {
            return "tls";
        }
        if (failure instanceof SocketTimeoutException || failure instanceof ConnectException) {
            return "tcp_connect";
        }
        if (failure instanceof ProtocolException || httpStatus >= 400) {
            return "websocket_upgrade";
        }
        return "websocket_closed";
    }
}
