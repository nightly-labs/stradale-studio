import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from studio.queue_store import QueueStore, publish_png


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'photos'; self.source.mkdir()
        self.destination = self.root / 'output'; self.destination.mkdir()
        self.store = QueueStore(self.root / 'state')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def add_photo(self, name='car.png'):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (100, 50), 'gray').save(path)
        return path

    def enqueue(self, **kw):
        return self.store.add_folder(self.source, self.destination, 'VEHIS', 'STRADALE', **kw)

    def wait_for(self, state, count, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.store.snapshot()['counts'][state] == count:
                return
            time.sleep(.01)
        self.fail(str(self.store.snapshot()['counts']))

    def test_nested_paths_and_no_extension_collision(self):
        self.add_photo('nested/car.jpg'); self.add_photo('nested/car.png')
        batch = self.enqueue()
        self.assertEqual(batch['count'], 2)
        jobs = [self.store.get_job(row['id']) for row in self.store.snapshot()['rows']]
        self.assertNotEqual(jobs[0]['output'], jobs[1]['output'])
        self.assertTrue(all('/nested/' in job['output'] for job in jobs))
        with self.assertRaises(ValueError):
            self.store.add_folder(self.source, self.source, 'VEHIS', 'STRADALE')

    def test_skip_symlinks_and_optionally_subfolders(self):
        self.add_photo(); self.add_photo('nested/other.png')
        (self.source/'link.png').symlink_to(self.source/'car.png')
        self.assertEqual(self.enqueue(recursive=False)['count'], 1)

    def test_recovery_restores_interrupted_jobs_and_starts_paused(self):
        self.add_photo(); self.enqueue()
        job = self.store.claim()
        self.assertEqual(self.store.get_job(job['id'])['state'], 'running')
        self.store.close()
        self.store = QueueStore(self.root / 'state')
        self.assertTrue(self.store.paused)
        self.assertEqual(self.store.get_job(job['id'])['state'], 'pending')

    def test_changed_source_is_rejected(self):
        photo = self.add_photo(); self.enqueue()
        job = self.store.claim(); photo.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.store.checked_source(job)

    def test_publish_never_overwrites(self):
        output = self.destination / 'result.png'
        output.write_bytes(b'existing')
        with self.assertRaises(FileExistsError):
            publish_png(np.zeros((10,10,3), np.uint8), output)
        self.assertEqual(output.read_bytes(), b'existing')
        self.assertEqual(list(self.destination.iterdir()), [output])

    def test_bounded_workers_pause_and_failure_isolation(self):
        for i in range(10): self.add_photo(f'{i}.png')
        self.enqueue()
        gate = threading.Event(); lock = threading.Lock(); active = 0; maximum = 0
        def process(job):
            nonlocal active, maximum
            with lock: active += 1; maximum = max(maximum, active)
            gate.wait(2)
            with lock: active -= 1
            if job['relative'] == '0.png': raise ValueError('Damaged image')
            return 'done', None, None
        self.store.processor = process
        self.store.start(); self.wait_for('running', 2)
        self.store.pause(); gate.set()
        self.wait_for('running', 0)
        self.assertEqual(self.store.snapshot()['counts']['pending'], 8)
        self.store.start(); self.wait_for('done', 9)
        self.assertEqual(self.store.snapshot()['counts']['failed'], 1)
        self.assertEqual(maximum, 2)

    def test_worker_setting_validation_and_restart(self):
        for value in (0, 51, -1, 2.5, '4', True, None):
            with self.assertRaises(ValueError): self.store.set_workers(value)
        self.store.set_workers(50)
        self.assertTrue(self.store.paused)
        self.store.close(); self.store = QueueStore(self.root / 'state')
        self.assertEqual(self.store.workers, 50)
        self.assertTrue(self.store.paused)
        self.store.set_workers(1)
        self.store.close(); self.store = QueueStore(self.root / 'state')
        self.assertEqual(self.store.workers, 1)

    def test_live_worker_increase_and_decrease(self):
        for i in range(55): self.add_photo(f'{i:02d}.png')
        self.enqueue()
        gates = [threading.Event() for _ in range(50)]
        def process(job):
            if job['id'] <= 50: gates[job['id']-1].wait(10)
            return 'done', None, None
        self.store.processor = process
        try:
            self.store.set_workers(1); self.store.start(); self.wait_for('running', 1)
            self.assertEqual(self.store.snapshot()['counts']['pending'], 54)
            self.store.set_workers(50); self.wait_for('running', 50, timeout=10)
            self.store.set_workers(1)
            for gate in gates[:49]: gate.set()
            self.wait_for('running', 1)
            self.assertEqual(self.store.snapshot()['counts']['pending'], 5)
            self.store.pause(); gates[49].set(); self.wait_for('running', 0)
            self.assertEqual(self.store.snapshot()['counts']['pending'], 5)
            self.store.start(); self.wait_for('done', 55)
        finally:
            for gate in gates: gate.set()

    def test_import_10000_is_paginated_and_persistent(self):
        for i in range(10000): (self.source/f'car-{i:05d}.jpg').touch()
        batch = self.enqueue()
        self.assertEqual(batch['count'], 10000)
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot['counts']['pending'], 10000)
        self.assertEqual(len(snapshot['rows']), 50)
        self.assertNotEqual(snapshot['rows'][0]['id'], self.store.snapshot(page=1)['rows'][0]['id'])
        self.store.close(); self.store = QueueStore(self.root/'state')
        self.assertEqual(self.store.snapshot()['counts']['pending'], 10000)

    def test_existing_output_after_crash_requires_review(self):
        self.add_photo(); self.enqueue(); job = self.store.claim()
        Path(job['output']).parent.mkdir(exist_ok=True, parents=True)
        Path(job['output']).write_bytes(b'already saved')
        state, _, _ = self.store.process_image(job)
        self.assertEqual(state, 'review')
        self.assertEqual(Path(job['output']).read_bytes(), b'already saved')

    def test_manual_repair_and_report(self):
        original = self.add_photo(); before = original.read_bytes(); self.enqueue(); job = self.store.claim()
        with self.store.db() as db: db.execute("UPDATE jobs SET state='review' WHERE id=?", (job['id'],))
        self.store.manual(job['id'], [[10,10],[90,10],[90,40],[10,40]], 'CUSTOM')
        self.assertEqual(self.store.get_job(job['id'])['state'], 'done')
        self.assertEqual(self.store.get_job(job['id'])['replacement'], 'CUSTOM')
        self.assertTrue(Path(job['output']).exists())
        self.assertEqual(original.read_bytes(), before)
        self.assertIn('done', self.store.report())

if __name__ == '__main__': unittest.main()
