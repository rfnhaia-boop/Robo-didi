import os
import tempfile
import unittest
from pathlib import Path

os.environ['ADMIN_TOKEN']='test-secret'
os.environ['PAPER_DB']=str(Path(tempfile.gettempdir())/'robo-didi-api-test.db')
from fastapi.testclient import TestClient
from backend.main import app


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)

    def test_status_is_public_and_versioned(self):
        r=self.client.get('/api/status')
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['versao_motor'],'paper-v2.0')

    def test_mutation_requires_admin_token(self):
        r=self.client.post('/api/pausar')
        self.assertEqual(r.status_code,401)
        r=self.client.post('/api/pausar',headers={'X-Admin-Token':'test-secret'})
        self.assertEqual(r.status_code,200)

    def test_bad_risk_is_rejected(self):
        r=self.client.post('/api/paper/config?estrategia=A',json={'risco_pct':20},
                           headers={'X-Admin-Token':'test-secret'})
        self.assertEqual(r.status_code,422)


if __name__=='__main__':
    unittest.main()
