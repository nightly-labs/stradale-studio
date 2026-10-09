"""Persistent, bounded queue. Source files are read only; output is exclusive."""
from contextlib import contextmanager
import csv
import io
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from PIL import Image, UnidentifiedImageError
from studio.engine import decode, detect, render

EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}
STATES = ('pending', 'running', 'done', 'review', 'failed')


def validate_text(text):
    if not isinstance(text, str) or not text.strip() or len(text.strip()) > 16 or any(
        not (c.isascii() and (c.isalnum() or c in ' -')) for c in text
    ):
        raise ValueError('Use 1–16 letters, numbers, spaces, or hyphens.')
    return text.strip()


def read_image(path):
    if path.stat().st_size > 60 * 1024 * 1024:
        raise ValueError('Image exceeds the 60 MB file limit.')
    try:
        return decode(path.read_bytes())
    except UnidentifiedImageError as exc:
        raise ValueError('This file is damaged or is not a supported image.') from exc


def publish_png(rgb, destination):
    """Publish atomically without replacing any existing file, including on recovery."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f'.{destination.name}.{uuid.uuid4().hex}.tmp')
    try:
        with temp.open('xb') as handle:
            Image.fromarray(rgb).save(handle, format='PNG')
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp, destination)  # Atomic and fails if a destination already exists.
    finally:
        temp.unlink(missing_ok=True)


class QueueStore:
    def __init__(self, directory, workers=2, processor=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / 'queue.sqlite3'
        self.workers = max(1, min(int(workers), 4))
        self.processor = processor or self.process_image
        self.condition = threading.Condition(threading.RLock())
        self.paused = True
        self.closed = False
        self.threads = []
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, output TEXT NOT NULL,
                    target TEXT NOT NULL, replacement TEXT NOT NULL, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
                    source TEXT NOT NULL, relative TEXT NOT NULL, output TEXT NOT NULL UNIQUE,
                    size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending', error TEXT, milliseconds INTEGER,
                    corners TEXT, updated REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_state_id ON jobs(state,id);
                CREATE INDEX IF NOT EXISTS jobs_batch ON jobs(batch_id);
            ''')
            if "replacement_override" not in {r[1] for r in db.execute("PRAGMA table_info(jobs)")}:
                db.execute("ALTER TABLE jobs ADD COLUMN replacement_override TEXT")
            db.execute("UPDATE jobs SET state='pending',error='Resumed after an interrupted run.' WHERE state='running'")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_folder(self, source, destination, target, replacement, recursive=True):
        source, destination = Path(source).expanduser().resolve(), Path(destination).expanduser().resolve()
        target, replacement = validate_text(target), validate_text(replacement)
        if not source.is_dir() or not destination.is_dir():
            raise ValueError('Choose an existing source folder and output folder.')
        # Prevent generated output from being scanned in this or later batches.
        if destination == source or destination.is_relative_to(source):
            raise ValueError('Choose an output folder outside the source folder.')
        identifier = uuid.uuid4().hex
        output = destination / f'Stradale-{time.strftime("%Y%m%d-%H%M%S")}-{identifier[:8]}'
        records = []
        def scan_error(error):
            raise ValueError(f'Cannot read folder: {error.filename}')
        for root, directories, files in os.walk(source, followlinks=False, onerror=scan_error):
            directories[:] = sorted(d for d in directories if not d.startswith('.') and not (Path(root)/d).is_symlink()) if recursive else []
            for name in sorted(files):
                path = Path(root) / name
                if name.startswith('.') or path.suffix.lower() not in EXTENSIONS or path.is_symlink():
                    continue
                stat = path.stat()
                relative = path.relative_to(source)
                # Keep the original extension in the name to prevent jpg/png collisions.
                dest = output / relative.parent / (relative.name + '.png')
                records.append((identifier, str(path), str(relative), str(dest), stat.st_size, stat.st_mtime_ns, time.time()))
        if not records:
            raise ValueError('No PNG, JPEG, or WebP images were found in that folder.')
        output.mkdir(exist_ok=False)
        with self.condition, self.db() as db:
            db.execute('INSERT INTO batches VALUES (?,?,?,?,?,?)',
                       (identifier, str(source), str(output), target, replacement, time.time()))
            db.executemany('INSERT INTO jobs (batch_id,source,relative,output,size,mtime_ns,updated) VALUES (?,?,?,?,?,?,?)', records)
            self.condition.notify_all()
        return {'id': identifier, 'count': len(records), 'output': str(output)}

    def start(self):
        with self.condition:
            if not self.threads:
                for number in range(self.workers):
                    thread = threading.Thread(target=self.worker, name=f'plate-worker-{number}', daemon=True)
                    self.threads.append(thread)
                    thread.start()
            self.paused = False
            self.condition.notify_all()

    def pause(self):
        with self.condition:
            self.paused = True

    def close(self):
        with self.condition:
            self.closed = True
            self.paused = True
            self.condition.notify_all()
        for thread in self.threads:
            thread.join(timeout=40)

    def claim(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT jobs.*,batches.target,COALESCE(jobs.replacement_override,batches.replacement) AS replacement FROM jobs JOIN batches ON batches.id=jobs.batch_id WHERE state='pending' ORDER BY jobs.id LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET state='running',error=NULL,updated=? WHERE id=?", (time.time(), row['id']))
                return dict(row)

    def worker(self):
        while True:
            with self.condition:
                while self.paused and not self.closed:
                    self.condition.wait()
                if self.closed:
                    return
                job = self.claim()
                if not job:
                    self.condition.wait(timeout=1)
                    continue
            start = time.perf_counter()
            try:
                state, error, corners = self.processor(job)
            except Exception as exc:
                state, error, corners = 'failed', str(exc)[:1000], None
            with self.db() as db:
                db.execute('UPDATE jobs SET state=?,error=?,corners=?,milliseconds=?,updated=? WHERE id=?',
                           (state, error, json.dumps(corners) if corners is not None else None,
                            round((time.perf_counter()-start)*1000), time.time(), job['id']))

    @staticmethod
    def checked_source(job):
        path = Path(job['source'])
        if path.is_symlink():
            raise ValueError('Source became a link. Add the folder again.')
        stat = path.stat()
        if stat.st_size != job['size'] or stat.st_mtime_ns != job['mtime_ns']:
            raise ValueError('Source changed after it was queued. Add the folder again.')
        return path

    def process_image(self, job):
        source = self.checked_source(job)
        if Path(job['output']).exists():
            return 'review', 'An output file already exists. Check it in Finder; it was not overwritten.', None
        rgb = read_image(source)
        match = detect(rgb, job['target'])
        if not match:
            return 'review', 'No single clear plate match. Select the corners manually.', None
        result = render(rgb, match['corners'], job['replacement'])
        self.checked_source(job)
        publish_png(result, job['output'])
        return 'done', None, match['corners']

    def get_job(self, identifier):
        with self.db() as db:
            row = db.execute('SELECT jobs.*,batches.target,COALESCE(jobs.replacement_override,batches.replacement) AS replacement FROM jobs JOIN batches ON batches.id=jobs.batch_id WHERE jobs.id=?', (identifier,)).fetchone()
        if not row:
            raise ValueError('Image was not found in this queue.')
        return dict(row)

    def manual(self, identifier, corners, text):
        text = validate_text(text)
        with self.condition, self.db() as db:
            job = self.get_job(identifier)
            if job['state'] not in ('review', 'failed'):
                raise ValueError('Manual repair is available for review or failed images only.')
            db.execute("UPDATE jobs SET state='running' WHERE id=?", (identifier,))
        try:
            rgb = read_image(self.checked_source(job))
            result = render(rgb, corners, text)
            self.checked_source(job)
            publish_png(result, job['output'])
            with self.db() as db:
                db.execute("UPDATE jobs SET state='done',error=NULL,corners=?,replacement_override=?,updated=? WHERE id=?", (json.dumps(corners), text, time.time(), identifier))
        except Exception:
            with self.db() as db:
                db.execute('UPDATE jobs SET state=? WHERE id=?', (job['state'], identifier))
            raise

    def retry_failed(self):
        with self.condition, self.db() as db:
            db.execute("UPDATE jobs SET state='pending',error=NULL WHERE state='failed'")
            self.condition.notify_all()

    def snapshot(self, state='', page=0):
        if state and state not in STATES:
            raise ValueError('Unknown queue filter.')
        page = max(0, int(page))
        with self.db() as db:
            counts = {key: 0 for key in STATES}
            counts.update({r['state']: r['count'] for r in db.execute('SELECT state,count(*) AS count FROM jobs GROUP BY state')})
            where, args = ('WHERE state=?', [state]) if state else ('', [])
            total = db.execute(f'SELECT count(*) FROM jobs {where}', args).fetchone()[0]
            rows = [dict(r) for r in db.execute(f'SELECT id,relative,state,error,milliseconds,batch_id FROM jobs {where} ORDER BY id DESC LIMIT 50 OFFSET ?', args+[page*50])]
            batches = [dict(r) for r in db.execute('SELECT * FROM batches ORDER BY created DESC LIMIT 20')]
        return {'counts': counts, 'rows': rows, 'total': total, 'page': page, 'batches': batches, 'paused': self.paused, 'workers': self.workers}

    def report(self):
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['source', 'output', 'status', 'message', 'milliseconds'])
        with self.db() as db:
            for row in db.execute('SELECT source,output,state,error,milliseconds FROM jobs ORDER BY id'):
                # Prevent formula execution when the report is opened in a spreadsheet.
                writer.writerow(["'"+str(v) if str(v or '').startswith(('=', '+', '-', '@', '\t', '\r')) else v for v in row])
        return output.getvalue()
