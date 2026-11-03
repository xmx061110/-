"""Collect existing acceptance evidence and recheck the downloaded SQL/replay."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from zhice.lade_query import connect
from zhice.lade_replay import verify_raw_replay

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    reports = ROOT / 'reports'
    read = lambda name: json.loads((reports / name).read_text(encoding='utf8'))
    imported = read('lade-import-validation.json')
    evaluated = read('lade-replay-evaluation.json')['summary']
    browser = read('lade-browser-validation.json')
    downloaded = read('LaDe浏览器下载证据.json')
    for source in (imported, evaluated, browser):
        assert source['status'] == 'passed'
    database = ROOT / 'data/lade/warehouse.sqlite'
    assert database.stat().st_size == imported['database_bytes']
    for name, expected in evaluated['code_sha256'].items():
        assert sha256(ROOT / name) == expected, f'Evaluation code changed: {name}'
    assert evaluated['scenario_count'] == 200 and evaluated['policy_runs'] == 800
    tests = (reports / 'lade-integration-tests.log').read_text(encoding='utf8')
    assert 'Ran 93 tests' in tests and tests.rstrip().endswith('OK')
    verification = verify_raw_replay(database, downloaded['input'], downloaded)
    assert verification['status'] == 'passed'
    with closing(connect(database)) as connection:
        actual = dict(connection.execute(downloaded['historical_metrics']['sql']['standalone_raw_reference']).fetchone())
    assert actual == downloaded['historical_metrics']['reference_counts']
    screenshot = reports / 'LaDe数据库算法接入验证.png'
    assert screenshot.stat().st_size > 10000
    original = ROOT / 'data/warehouse.sqlite'
    original_hash = sha256(original)
    assert original_hash == read('delivery-risk-evaluation.json')['data_sha256']
    evidence_names = ['lade-import-validation.json', 'lade-replay-evaluation.json',
                      'lade-browser-validation.json', 'lade-integration-tests.log',
                      'LaDe浏览器下载证据.json', 'LaDe数据库算法接入验证.png']
    result = {
        'status': 'passed', 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
        'database': str(database), 'database_bytes': database.stat().st_size,
        'source_count': len(imported['source_counts']),
        'scenario_count': evaluated['scenario_count'], 'policy_runs': evaluated['policy_runs'],
        'test_count': 93, 'browser_case_count': len(browser['cases']),
        'downloaded_replay_reverification': verification,
        'standalone_raw_sql_reverification': 'passed',
        'evaluation_code_hashes_match': True,
        'olist_database_unchanged_from_recorded_baseline': True,
        'olist_database_sha256': original_hash,
        'evidence_sha256': {name: sha256(reports / name) for name in evidence_names},
        'limitations': evaluated['limitations'] + [
            'LaDe与Olist独立存储，无共同订单映射；GPS标识未与城市快递员建立映射。',
            '未接入生产实时物流、华为云或完整原定四Agent编排。']}
    target = reports / 'lade-integration-acceptance.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({k: result[k] for k in ['status', 'source_count', 'database_bytes',
          'scenario_count', 'policy_runs', 'test_count', 'browser_case_count']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
