#!/usr/bin/env python
import re
import socket
import logging
import multiprocessing

from gevent import pool
from gevent import select
from gevent.server import StreamServer
from gevent import monkey
monkey.patch_socket()

BUF_SIZE = 4 * 1024
CRLF = b"\r\n"

logger = logging.getLogger(__name__)


class HttpRelayHandler(multiprocessing.Process):
    # pool_count max is 100000
    # ensure the proxy weight is max

    def __init__(self, queue, proxy=("127.0.0.1", 8399), pool_count=100):
        multiprocessing.Process.__init__(self)
        self._proxy = proxy
        self._queue = queue
        self._pool = pool.Pool(pool_count)
        self._cache = None
        self._server = StreamServer(
                proxy, self._handle_connection, spawn=self._pool)

    def _handle_connection(self, local_sock, address):
        if not self._cache:
            self._cache = self._queue.setup_cache
        cache = self._cache
        best_proxy = max(cache, key=cache.get)
        proxy_value = self._cache.get(best_proxy)
        logger.debug("proxy is {}, weight is {}"
                     .format(best_proxy, proxy_value))
        self._cache[best_proxy] = proxy_value * 0.5
        ip, port = best_proxy.split(":")

        try:
            remote_sock = self._create_remote_connection((ip, int(port)))
            while True:
                r, w, e = select.select(
                        [local_sock, remote_sock], [], [])
                if local_sock in r:
                    request_data = local_sock.recv(BUF_SIZE)
                    if remote_sock.send(request_data) <= 0:
                        break

                if remote_sock in r:
                    response_data = remote_sock.recv(BUF_SIZE)
                    if local_sock.send(response_data) <= 0:
                        logger.debug("remote close connection")
                        remote_sock.close()
                        break
                    response = self._parse_response(response_data)
                    if response:
                        request = self._parse_request(request_data)
                        logger.info("({}) {} {}".format(
                            best_proxy, request, response))

            self._cache[best_proxy] = self._cache[best_proxy] / 0.5
        except Exception, e:
            # connection refused
            logger.error(e.message)

    def setup_cache(self):
        self._cache = self._queue.setup_cache

    def _parse_request(self, request_data):
        request_header = request_data.split(CRLF)[0]
        return request_header

    def _parse_response(self, response_data):
        header = response_data.split(CRLF)[0]
        if re.match(r"HTTP/\d\.\d", header):
            return header
        else:
            return None

    def _create_remote_connection(self, proxy):
        remote_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        remote_sock.connect(proxy)
        return remote_sock

    def run(self):
        logger.info("Starting local server on {}.".format(self._proxy))
        self._server.serve_forever()
