"""Token-protected local HTTP interface for the bundled Mac app."""
import argparse
import base64
import fcntl
import io
import os
from pathlib import Path
import secrets
import signal
import threading
import time

from flask import Flask, jsonify, make_response, redirect, request, send_file
from PIL import Image, UnidentifiedImageError
from werkzeug.exceptions import HTTPException
from werkzeug.serving import make_server, WSGIRequestHandler

from studio.engine import decode, detect, render
from studio.queue_store import QueueStore, read_image

ROOT = Path(__file__).parent


def create_app(store, token):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 36 * 1024 * 1024

    @app.before_request
    def local_only():
        if request.host.split(':')[0] != '127.0.0.1':
            return jsonify(error='Use the local app address.'), 403
        if request.path == '/launch':
            if not secrets.compare_digest(request.args.get('token', ''), token):
                return jsonify(error='Open the app to start a local session.'), 403
            response = make_response(redirect('/'))
            response.set_cookie('stradale_session', token, httponly=True, samesite='Strict')
            return response
        if not secrets.compare_digest(request.cookies.get('stradale_session', ''), token):
            return jsonify(error='Open the app to start a local session.'), 403
        if request.method == 'POST':
            if request.headers.get('Origin') not in (None, request.host_url.rstrip('/')):
                return jsonify(error='Request origin is not allowed.'), 403
            if not request.is_json:
                return jsonify(error='Use a JSON request.'), 415

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.get('/')
    def index():
        return send_file(ROOT / 'ui/index.html')

    @app.get('/assets/<name>')
    def asset(name):
        if name not in ('app.js', 'style.css'):
            return '', 404
        return send_file(ROOT / 'ui' / name)

    @app.get('/api/queue')
    def snapshot():
        return jsonify(store.snapshot(request.args.get('state', ''), request.args.get('page', 0)))

    @app.post('/api/folders')
    def add_folder():
        data = request.get_json()
        return jsonify(store.add_folder(data['source'], data['destination'], data['target'], data['replacement'], bool(data.get('recursive', True))))

    @app.post('/api/queue/<action>')
    def queue_action(action):
        if action == 'start':
            store.start()
        elif action == 'pause':
            store.pause()
        elif action == 'workers':
            store.set_workers(request.get_json().get('workers'))
        elif action == 'retry':
            store.retry_failed()
        else:
            raise ValueError('Unknown queue action.')
        return jsonify(ok=True)

    @app.get('/api/report')
    def report():
        return send_file(io.BytesIO(store.report().encode('utf-8-sig')), mimetype='text/csv', as_attachment=True, download_name='stradale-report.csv')

    @app.get('/api/jobs/<int:identifier>')
    def job(identifier):
        return jsonify(store.get_job(identifier))

    @app.get('/api/jobs/<int:identifier>/image')
    def job_image(identifier):
        job = store.get_job(identifier)
        path = Path(job['output']) if request.args.get('result') == '1' and job['state'] == 'done' else store.checked_source(job)
        # Normalize orientation and decode before the editor gets the image.
        stream = io.BytesIO()
        Image.fromarray(read_image(path)).save(stream, format='PNG')
        stream.seek(0)
        return send_file(stream, mimetype='image/png')

    @app.post('/api/jobs/<int:identifier>/manual')
    def manual(identifier):
        data = request.get_json()
        store.manual(identifier, data['corners'], data['text'])
        return jsonify(ok=True)

    def image_data():
        data = request.get_json()
        if not isinstance(data, dict) or not isinstance(data.get('image'), str):
            raise ValueError('Choose an image first.')
        return data, decode(base64.b64decode(data['image'], validate=True))

    @app.post('/api/detect')
    def detection():
        start = time.perf_counter()
        data, rgb = image_data()
        target = data.get('target', 'VEHIS').strip()
        if not target or len(target) > 16:
            raise ValueError('Enter the text to find, with no more than 16 characters.')
        return jsonify(match=detect(rgb, target), milliseconds=round((time.perf_counter()-start)*1000))

    @app.post('/api/render')
    def rendering():
        start = time.perf_counter()
        data, rgb = image_data()
        result = render(rgb, data.get('corners'), data.get('text', 'STRADALE'))
        stream = io.BytesIO()
        Image.fromarray(result).save(stream, format='PNG')
        return jsonify(image=base64.b64encode(stream.getvalue()).decode(), milliseconds=round((time.perf_counter()-start)*1000))

    @app.errorhandler(Exception)
    def error(exc):
        if isinstance(exc, HTTPException):
            return jsonify(error=exc.description), exc.code
        if isinstance(exc, (ValueError, KeyError, TypeError, UnidentifiedImageError, OSError)):
            return jsonify(error=str(exc)), 400
        app.logger.exception('Local processing failed')
        return jsonify(error='Local processing failed. Check the app log, or select corners manually.'), 500
    return app


class QuietRequests(WSGIRequestHandler):
    def log_request(self, code='-', size='-'):
        pass  # Do not log local session tokens or photo paths.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', default=str(Path.home() / 'Library/Application Support/Stradale Studio'))
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--workers', type=int, default=None)
    parser.add_argument('--parent-pid', type=int)
    args = parser.parse_args()
    directory = Path(args.data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    lock = (directory / 'instance.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('This queue is already open in another app instance.')
    store = QueueStore(directory, args.workers)
    token = os.environ.get('STRADALE_TOKEN') or secrets.token_urlsafe(32)
    server = make_server('127.0.0.1', args.port, create_app(store, token), threaded=True, request_handler=QuietRequests)
    stopping = threading.Event()

    def stop(*_):
        if not stopping.is_set():
            stopping.set()
            store.pause()
            threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if args.parent_pid:
        def watch_parent():
            while not stopping.wait(2):
                if os.getppid() != args.parent_pid:
                    stop()
                    return
        threading.Thread(target=watch_parent, daemon=True).start()
    print(f'READY http://127.0.0.1:{server.server_port}/launch?token={token}', flush=True)
    try:
        server.serve_forever()
    finally:
        store.close()
        server.server_close()
        lock.close()


if __name__ == '__main__':
    main()
