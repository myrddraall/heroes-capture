"""A pack served on this computer, for its reference viewer (heroes-capture map view): a browser
reads a PMTiles archive by HTTP range requests, which it won't make to files opened from disk,
and Python's own file server doesn't answer them. Local only (127.0.0.1)."""

import http.server
import re
import threading
import webbrowser
from functools import partial
from pathlib import Path


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Files from the pack's folder, with byte ranges ("Range: bytes=a-b") answered."""

    def send_head(self):
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        path = Path(self.translate_path(self.path))
        if not match or not path.is_file():
            return super().send_head()
        size = path.stat().st_size
        start, end = match.group(1), match.group(2)
        first = int(start) if start else max(0, size - int(end))
        last = min(size - 1, int(end)) if start and end else size - 1
        if first > last or first >= size:
            self.send_error(416, "Range Not Satisfiable")
            return None
        f = open(path, "rb")
        f.seek(first)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Content-Range", f"bytes {first}-{last}/{size}")
        self.send_header("Content-Length", str(last - first + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self._remaining = last - first + 1
        return f

    def copyfile(self, source, outputfile):
        remaining = getattr(self, "_remaining", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        while remaining > 0:
            chunk = source.read(min(remaining, 1 << 16))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)
        self._remaining = None

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")  # a fresh render shows at once
        super().end_headers()

    def log_message(self, *args):
        pass  # quiet: the viewer asks for many tiles


def serve(folder: Path, open_browser: bool = True) -> http.server.ThreadingHTTPServer:
    """`folder` served on a free local port, in a thread; the browser opened on its index.html.
    Returns the server (its url is http://127.0.0.1:<server.server_port>/)."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, directory=str(folder)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{server.server_port}/index.html")
    return server
