import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zhice.model_settings import resolve_model_config


class ModelSettingsTests(unittest.TestCase):
    def test_local_config_survives_without_old_share_zip_and_env_takes_priority(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'model.json';path.write_text(json.dumps({'url':'https://example.test/v1/chat/completions','key':'local-test','model':'local-model'}),encoding='utf8')
            with patch.dict('os.environ',{},clear=True):
                self.assertEqual(resolve_model_config(path),('https://example.test/v1/chat/completions','local-test','local-model'))
            with patch.dict('os.environ',{'ZHICE_MODEL_KEY':'env-test','ZHICE_MODEL_NAME':'env-model'},clear=True):
                self.assertEqual(resolve_model_config(path)[1:],('env-test','env-model'))

    def test_invalid_config_error_does_not_expose_content(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'bad.json';path.write_text('{"key":"PRIVATE-TEST"',encoding='utf8')
            with self.assertRaisesRegex(ValueError,'无法读取') as caught:resolve_model_config(path)
            self.assertNotIn('PRIVATE-TEST',str(caught.exception))
