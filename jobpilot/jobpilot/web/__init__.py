"""Local web page for browsing collected jobs and picking which to pursue.

`jobpilot serve` starts a small HTTP server on 127.0.0.1 (never on other
interfaces) that serves one page plus a JSON API over the SQLite store.
Jobs checked on the page are saved as selections, which
`jobpilot match --selected` and `jobpilot apply --selected` act on.
"""
import http.server
import json
import logging
import os
import threading
from typing import Optional
from urllib.parse import parse_qs, urlparse
import webbrowser

from jobpilot.store import Store

logger = logging.getLogger(__name__)

_INDEX = os.path.join(os.path.dirname(__file__), 'index.html')
_MAX_BODY = 1 << 20


def make_handler(store: Store, info: dict, scorer=None):
  lock = threading.Lock()
  quick = {}  # uid -> local fit estimate, computed once per server run

  class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'JobPilot'

    def log_message(self, fmt, *args):  # quieter than the default
      logger.debug('%s - %s', self.address_string(), fmt % args)

    def _send(self, code: int, body: bytes, ctype: str):
      self.send_response(code)
      self.send_header('Content-Type', ctype)
      self.send_header('Content-Length', str(len(body)))
      self.send_header('Cache-Control', 'no-store')
      self.send_header('X-Content-Type-Options', 'nosniff')
      self.send_header('Referrer-Policy', 'no-referrer')
      self.end_headers()
      self.wfile.write(body)

    def _json(self, code: int, obj):
      self._send(code,
                 json.dumps(obj, ensure_ascii=False).encode(),
                 'application/json; charset=utf-8')

    def _same_origin(self) -> bool:
      """Rejects writes from other sites open in the same browser."""
      host = self.headers.get('Host', '')
      origin = self.headers.get('Origin')
      ok_hosts = {
          f'127.0.0.1:{self.server.server_port}',
          f'localhost:{self.server.server_port}'
      }
      if host not in ok_hosts:
        return False
      return origin is None or urlparse(origin).netloc in ok_hosts

    def do_GET(self):  # pylint: disable=invalid-name
      url = urlparse(self.path)
      if url.path in ('/', '/index.html'):
        with open(_INDEX, 'rb') as f:
          self._send(200, f.read(), 'text/html; charset=utf-8')
      elif url.path == '/api/jobs':
        with lock:
          jobs = store.web_jobs()
          if scorer and any(j['uid'] not in quick for j in jobs):
            for job in store.job_texts():
              if job.uid not in quick:
                quick[job.uid] = scorer(job)
        for j in jobs:
          j['quick'] = quick.get(j['uid'])
        self._json(200, {'jobs': jobs, **info})
      elif url.path == '/cv':
        uid = (parse_qs(url.query).get('uid') or [''])[0]
        with lock:
          cv = store.cv(uid)
        path = os.path.join(cv[0], 'cv.html') if cv else ''
        if not os.path.isfile(path):
          return self._json(404, {'error': 'no CV for this job'})
        with open(path, 'rb') as f:
          self._send(200, f.read(), 'text/html; charset=utf-8')
      elif url.path == '/api/job':
        uid = (parse_qs(url.query).get('uid') or [''])[0]
        with lock:
          detail = store.web_job_detail(uid)
        self._json(200, detail) if detail else self._json(404, {})
      else:
        self._json(404, {'error': 'not found'})

    def do_POST(self):  # pylint: disable=invalid-name
      if urlparse(self.path).path != '/api/select':
        return self._json(404, {'error': 'not found'})
      if not self._same_origin():
        return self._json(403, {'error': 'cross-origin request refused'})
      if 'application/json' not in self.headers.get('Content-Type', ''):
        return self._json(415, {'error': 'expected application/json'})
      length = int(self.headers.get('Content-Length') or 0)
      if length > _MAX_BODY:
        return self._json(413, {'error': 'too large'})
      try:
        body = json.loads(self.rfile.read(length) or b'{}')
        uids = [str(u) for u in body['uids']]
        selected = bool(body['selected'])
      except (ValueError, KeyError, TypeError):
        return self._json(400, {'error': 'expected {uids: [...], selected}'})
      with lock:
        store.set_selected(uids, selected)
        count = len(store.selected_uids())
      self._json(200, {'selected_count': count})

  return Handler


def make_server(store: Store,
                port: int = 8765,
                info: Optional[dict] = None,
                scorer=None) -> http.server.HTTPServer:
  return http.server.ThreadingHTTPServer(('127.0.0.1', port),
                                         make_handler(store, info or {},
                                                      scorer))


def serve(store: Store,
          port: int = 8765,
          open_browser: bool = True,
          info: Optional[dict] = None,
          scorer=None):
  try:
    server = make_server(store, port, info, scorer)
  except OSError as e:
    raise SystemExit(f'cannot listen on 127.0.0.1:{port} ({e.strerror}); '
                     'try --port 8766') from e
  url = f'http://127.0.0.1:{server.server_port}/'
  print(f'JobPilot is running at {url}  (Ctrl+C to stop)')
  if open_browser:
    threading.Timer(0.5, webbrowser.open, (url,)).start()
  try:
    server.serve_forever()
  except KeyboardInterrupt:
    print('\nstopped')
  finally:
    server.server_close()
