import tempfile
import unittest
from studio.queue_store import QueueStore
from studio.server import create_app

class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = QueueStore(self.temp.name)
        self.client = create_app(self.store, 'test-token').test_client()
    def tearDown(self):
        self.store.close(); self.temp.cleanup()
    def request(self, method, path, **kw):
        return getattr(self.client, method)(path, base_url='http://127.0.0.1:9999', **kw)
    def login(self):
        self.assertEqual(self.request('get', '/launch?token=test-token').status_code, 302)
    def test_requires_session(self):
        self.assertEqual(self.request('get', '/api/queue').status_code, 403)
        self.login()
        self.assertEqual(self.request('get', '/api/queue').status_code, 200)
    def test_cross_origin_rejected(self):
        self.login()
        self.assertEqual(self.request('post', '/api/queue/start', json={}, headers={'Origin':'https://example.com'}).status_code, 403)
        self.assertEqual(self.request('post', '/api/queue/start', data='x').status_code, 415)
    def test_invalid_input_stays_json(self):
        self.login()
        result = self.request('post', '/api/render', json={})
        self.assertEqual(result.status_code, 400)
        self.assertIn('error', result.json)
    def test_dns_rebinding_host_rejected(self):
        self.assertEqual(self.client.get('/launch?token=test-token', base_url='http://example.com:9999').status_code, 403)

    def test_worker_setting_api(self):
        self.login()
        for value in (1, 50):
            result = self.request('post', '/api/queue/workers', json={'workers': value})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(self.request('get', '/api/queue').json['workers'], value)
        for value in (0, 51, 2.5, True, '3', None):
            result = self.request('post', '/api/queue/workers', json={'workers': value})
            self.assertEqual(result.status_code, 400)
        self.assertEqual(self.store.workers, 50)
