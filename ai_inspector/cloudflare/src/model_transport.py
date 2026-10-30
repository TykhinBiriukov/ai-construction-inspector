"""Bounded Workers Fetch transport for the server-only Meta adapter."""

import httpx
import js
from pyodide.ffi import JsException, jsnull, run_sync, to_js


class _Deadline:
    def __init__(self, timeout):
        self.controller = js.AbortController.new()
        self.timer = js.setTimeout(self.controller.abort.bind(self.controller), int(timeout * 1000))

    def close(self):
        js.clearTimeout(self.timer)
        self.controller.abort()


class _ResponseStream(httpx.SyncByteStream):
    def __init__(self, reader, deadline, request):
        self.reader = reader
        self.deadline = deadline
        self.request = request

    def __iter__(self):
        while self.reader is not None:
            try:
                chunk = run_sync(self.reader.read())
            except JsException:
                kind = httpx.ReadTimeout if self.deadline.controller.signal.aborted else httpx.ReadError
                raise kind("Model response could not be read", request=self.request) from None
            if chunk.done:
                return
            yield bytes(chunk.value.to_py())

    def close(self):
        if self.reader is not None:
            reader, self.reader = self.reader, None
            self.deadline.close()
            try:
                run_sync(reader.cancel())
                reader.releaseLock()
            except JsException:
                pass


class WorkerModelTransport(httpx.BaseTransport):
    """Keep Fetch redirects manual; credentials must never follow a Location."""

    def handle_request(self, request):
        timeout = request.extensions.get("timeout", {}).get("read") or 30.0
        deadline = _Deadline(timeout)
        try:
            options = {
                "method": request.method,
                "headers": dict(request.headers),
                "redirect": "manual",
                "signal": deadline.controller.signal,
            }
            if request.method not in ("GET", "HEAD"):
                options["body"] = request.read()
            response = run_sync(js.fetch(str(request.url), to_js(options, dict_converter=js.Object.fromEntries)))
        except JsException:
            kind = httpx.ConnectTimeout if deadline.controller.signal.aborted else httpx.ConnectError
            deadline.close()
            raise kind("Model connection was unsuccessful", request=request) from None
        headers = dict(response.headers.entries())
        # Fetch has already decoded the response body. Avoid decoding it again.
        headers.pop("content-encoding", None)
        headers.pop("content-length", None)
        if response.body is None or response.body is jsnull:
            deadline.close()
            stream = httpx.ByteStream(b"")
        else:
            stream = _ResponseStream(response.body.getReader(), deadline, request)
        return httpx.Response(response.status, headers=headers, stream=stream, request=request)
