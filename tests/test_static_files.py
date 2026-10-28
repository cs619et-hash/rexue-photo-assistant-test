from pathlib import Path
import json
import unittest

ROOT = Path(__file__).resolve().parents[1]
class TestStaticFiles(unittest.TestCase):
    def test_basic_files_exist(self):
        for name in ('web/index.html', 'web/app.js', 'web/style.css', 'web/firebase-config.js',
                     'firestore.rules', 'firebase.json'):
            self.assertTrue((ROOT / name).is_file(), name)
    def test_public_firebase_config_uninitialized(self):
        config = (ROOT / 'web/firebase-config.js').read_text(encoding='utf-8')
        self.assertIn('REXUE_FIREBASE_CONFIG', config)
    def test_app_wiring(self):
        html=(ROOT/'web/index.html').read_text(encoding='utf-8')
        for name in ('jobList', 'summaryCards', 'calendarPage', 'settingsPage'):
            self.assertIn(name,html)
    def test_deploy_config(self):
        cfg=json.loads((ROOT/'firebase.json').read_text(encoding='utf-8'))
        self.assertEqual(cfg['hosting']['public'],'web')
        self.assertEqual(cfg['firestore']['rules'],'firestore.rules')

if __name__ == '__main__':unittest.main()