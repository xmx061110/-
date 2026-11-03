import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zhice.lade_acquire import download_one, inspect_csv, local_path


class Response(BytesIO):
    def __init__(self, payload, status=200, headers=None):
        super().__init__(payload)
        self.status = status
        self.headers = headers or {}


class LaDeAcquireTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.payload = b'package_id,value\na,12\nb,34\n'
        self.entry = {'path': 'pickup/example.csv', 'size': len(self.payload),
                      'lfs': {'oid': hashlib.sha256(self.payload).hexdigest()}}

    def tearDown(self):
        self.directory.cleanup()

    def make_partial(self):
        partial = self.root / 'pickup/example.csv.part'
        partial.parent.mkdir(parents=True)
        partial.write_bytes(self.payload[:7])
        return partial

    def test_range_resume_preserves_exact_bytes(self):
        self.make_partial()
        response = Response(self.payload[7:], 206, {'Content-Range': f'bytes 7-{len(self.payload)-1}/{len(self.payload)}'})
        with patch('zhice.lade_acquire.urllib.request.urlopen', return_value=response) as opened:
            result = download_one(self.root, self.entry)
        self.assertEqual(opened.call_args.args[0].get_header('Range'), f'bytes=7-{len(self.payload)-1}')
        self.assertEqual((self.root / self.entry['path']).read_bytes(), self.payload)
        self.assertEqual(result['sha256'], self.entry['lfs']['oid'])

    def test_ignored_range_restarts_instead_of_appending(self):
        self.make_partial()
        with patch('zhice.lade_acquire.urllib.request.urlopen', return_value=Response(self.payload)):
            download_one(self.root, self.entry)
        self.assertEqual((self.root / self.entry['path']).read_bytes(), self.payload)

    def test_existing_wrong_original_is_not_overwritten(self):
        target = self.root / self.entry['path']; target.parent.mkdir(parents=True)
        target.write_bytes(b'user-modified')
        with self.assertRaises(ValueError), patch('zhice.lade_acquire.urllib.request.urlopen') as opened:
            download_one(self.root, self.entry)
        self.assertEqual(target.read_bytes(), b'user-modified')
        opened.assert_not_called()

    def test_valid_original_is_reused_without_network(self):
        target = self.root / self.entry['path']; target.parent.mkdir(parents=True)
        target.write_bytes(self.payload)
        with patch('zhice.lade_acquire.urllib.request.urlopen') as opened:
            download_one(self.root, self.entry)
        opened.assert_not_called()

    def test_paths_cannot_escape_target_directory(self):
        for value in ('../outside.csv', '/absolute.csv', 'pickup/../../outside.csv', 'pickup\\outside.csv'):
            with self.subTest(path=value), self.assertRaises(ValueError):
                local_path(self.root, value)

    def test_csv_full_read_checks_structure_and_missing_identifier(self):
        target = self.root / 'file.csv'; target.write_bytes(self.payload)
        result = inspect_csv(target)
        self.assertEqual(result['rows'], 2)
        target.write_text('package_id,value\n,12\n', encoding='utf8')
        self.assertEqual(inspect_csv(target)['empty_identifier_count'], 1)
        target.write_text('order_id,value\n,12\n', encoding='utf8')
        self.assertEqual(inspect_csv(target)['identifier_field'], 'order_id')
        self.assertEqual(inspect_csv(target)['empty_identifier_count'], 1)
        target.write_text('road_id,value\n1,12\n', encoding='utf8')
        self.assertIsNone(inspect_csv(target)['empty_identifier_count'])
        target.write_text('a,b\n1,2,3\n', encoding='utf8')
        with self.assertRaises(ValueError): inspect_csv(target)

    def test_road_csv_extension_can_contain_tab_delimited_geometry(self):
        target = self.root / 'roads.csv'
        target.write_text('osm_id\tgeometry\n1\tLINESTRING (1 2, 3 4)\n', encoding='utf8')
        r = inspect_csv(target)
        self.assertEqual(r['delimiter'], 'tab')
        self.assertEqual(r['rows'], 1)
        self.assertEqual(r['columns'], ['osm_id', 'geometry'])
        self.assertEqual(r['malformed_rows'], 0)


if __name__ == '__main__':
    unittest.main()
