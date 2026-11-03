"""固定版本LaDe原始文件下载、断点续传与全文件校验；不会反序列化pickle。"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import hashlib
import http.client
import json
import lzma
from pathlib import Path, PurePosixPath
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'Cainiao-AI/LaDe'
REVISION = 'be2cec02775cafc8d52230303f32134382bcc50b'
BASE = 'https://huggingface.co'
AGENT = 'Zhice-public-dataset-archive/1.0'


def now():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.writing')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    temporary.replace(path)


def fetch_json(url):
    request = urllib.request.Request(url, headers={'User-Agent': AGENT})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def local_path(root, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or any(x in ('', '.', '..') for x in relative.parts) or '\\' in name:
        raise ValueError('源清单含不安全路径')
    result = root.joinpath(*relative.parts).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError('下载目标越出数据目录')
    return result


def fingerprints(path):
    sha256 = hashlib.sha256()
    blob = hashlib.sha1(f'blob {path.stat().st_size}\0'.encode())
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            sha256.update(chunk)
            blob.update(chunk)
    return sha256.hexdigest(), blob.hexdigest()


def matches(path, entry):
    if not path.is_file() or path.stat().st_size != entry['size']:
        return None
    sha256, blob = fingerprints(path)
    expected = entry.get('lfs', {}).get('oid')
    if (expected and sha256 != expected) or (not expected and blob != entry['oid']):
        return None
    return {'sha256': sha256, 'git_blob_sha1': blob,
            'verified_against': 'publisher_lfs_sha256' if expected else 'publisher_git_blob_sha1'}


def download_one(raw, entry):
    name = entry['path']
    target = local_path(raw, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        verified = matches(target, entry)
        if verified:
            print('已验证，复用：' + name, flush=True)
            return {'path': name, 'bytes': entry['size'], **verified}
        raise ValueError(f'已有原始文件与固定版本不同，拒绝覆盖：{name}')
    partial = target.with_name(target.name + '.part')
    source = f'{BASE}/datasets/{REPOSITORY}/resolve/{REVISION}/{urllib.parse.quote(name)}'
    last_error = None
    failures = 0
    while failures < 5:
        try:
            offset = partial.stat().st_size if partial.exists() else 0
            if offset > entry['size']:
                raise ValueError('部分文件长度超过发布方长度')
            if offset < entry['size']:
                headers = {'User-Agent': AGENT, 'Accept-Encoding': 'identity'}
                # 32MiB分段使连接超时或大文件尾段缓慢时，能够从已落盘字节继续。
                end = min(entry['size'] - 1, offset + 32 * 1024 * 1024 - 1)
                headers['Range'] = f'bytes={offset}-{end}'
                url = source + f'?download=true&segment={offset}&request={time.time_ns()}'
                request = urllib.request.Request(url, headers=headers)
                began = time.monotonic()
                with urllib.request.urlopen(request, timeout=30) as response:
                    status = response.status
                    if status == 206:
                        if not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                            raise ValueError('续传起始位置不匹配')
                        mode = 'ab' if offset else 'wb'
                    elif status == 200:
                        mode = 'wb'  # 服务端忽略Range时从头写，不能追加造成重复。
                    else:
                        raise ValueError(f'意外下载状态：{status}')
                    with partial.open(mode) as output:
                        received = 0
                        # read1不等待凑满1MiB，已收到的少量数据也立即保留。
                        for chunk in iter(lambda: response.read1(1024 * 1024), b''):
                            output.write(chunk)
                            received += len(chunk)
                            if output.tell() > entry['size']:
                                raise ValueError('下载长度超过清单长度')
                            elapsed = time.monotonic() - began
                            if elapsed > 60 or (elapsed > 30 and received / elapsed < 2048):
                                raise TimeoutError('分段连接过慢，保留已写入部分后重新续传')
                if partial.stat().st_size < entry['size']:
                    if partial.stat().st_size <= offset:
                        raise TimeoutError('分段没有推进')
                    failures = 0
                    continue
            verified = matches(partial, entry)
            if not verified:
                if partial.exists() and partial.stat().st_size == entry['size']:
                    # 仅删除本下载器自己的未通过哈希校验的.part，不触碰原始完成文件。
                    partial.unlink()
                raise ValueError('下载未完成或发布方哈希不一致')
            partial.replace(target)
            print(f'下载并校验完成：{name} ({entry["size"]:,} bytes)', flush=True)
            return {'path': name, 'bytes': entry['size'], **verified}
        except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException) as exc:
            last_error = exc
            failures += 1
            # 不输出可能包含临时签名参数的重定向地址。
            print(f'分段重试 {failures}/5：{name}，{type(exc).__name__}', flush=True)
            if failures < 5:
                time.sleep(min(2 ** failures, 12))
    raise RuntimeError(f'下载未完成：{name}；{type(last_error).__name__}')


def inspect_csv(path):
    rows = malformed = empty_identifiers = 0
    csv.field_size_limit(32 * 1024 * 1024)
    with path.open('r', encoding='utf-8-sig', newline='') as stream:
        # 发布方roads.csv实际为Tab分隔；几何列含逗号，不能按扩展名猜。
        header = stream.readline()
        delimiter = '\t' if '\t' in header and ',' not in header else ','
        stream.seek(0)
        reader = csv.reader(stream, delimiter=delimiter, strict=True)
        columns = next(reader)
        if len(columns) != len(set(columns)):
            raise ValueError(f'CSV表头重复：{path.name}')
        identifier_field = next((name for name in ('package_id', 'order_id') if name in columns), None)
        identifier_column = columns.index(identifier_field) if identifier_field else None
        for row in reader:
            rows += 1
            if len(row) != len(columns):
                malformed += 1
            elif identifier_column is not None and not row[identifier_column].strip():
                empty_identifiers += 1
    if malformed:
        raise ValueError(f'CSV行列数异常：{path.name}，{malformed}行')
    return {'rows': rows, 'columns': columns, 'column_count': len(columns),
            'delimiter': 'tab' if delimiter == '\t' else 'comma',
            'malformed_rows': malformed, 'identifier_field': identifier_field,
            'empty_identifier_count': empty_identifiers if identifier_field else None,
            'scope': '完整UTF-8与CSV解析；未清洗，尚未校验所有业务主键和日期语义'}


def inspect_container(path):
    if path.suffix == '.zip':
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError('ZIP CRC不一致：' + bad)
            return {'zip_members': len(archive.infolist()), 'crc': 'passed',
                    'uncompressed_bytes': sum(x.file_size for x in archive.infolist()), 'extracted': False}
    if path.suffix == '.xz':
        count = 0
        with lzma.open(path, 'rb') as stream:
            for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                count += len(block)
        return {'xz_stream_integrity': 'passed', 'uncompressed_bytes': count, 'pickle_deserialized': False}
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, choices=(1, 2, 3, 4), default=3)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    directory = ROOT / 'data/lade'
    raw, provenance = directory / 'raw', directory / 'provenance'
    raw.mkdir(parents=True, exist_ok=True)
    provenance.mkdir(parents=True, exist_ok=True)
    tree_path = provenance / 'official-tree.json'
    if args.verify_only:
        tree = json.loads(tree_path.read_text(encoding='utf8'))
    else:
        tree = fetch_json(f'{BASE}/api/datasets/{REPOSITORY}/tree/{REVISION}?recursive=true&expand=false')
        save_json(tree_path, tree)
        metadata = fetch_json(f'{BASE}/api/datasets/{REPOSITORY}/revision/{REVISION}')
        save_json(provenance / 'repository-metadata.json', metadata)
    files = [entry for entry in tree if entry['type'] == 'file']
    expected_names = {f'{kind}/{kind}_{city}.csv' for kind in ('pickup', 'delivery')
                      for city in ('cq', 'hz', 'jl', 'sh', 'yt')}
    if not expected_names.issubset({entry['path'] for entry in files}):
        raise ValueError('发布方清单缺少五城市揽收/派送文件')
    print(f'固定版本 {REVISION}，{len(files)}个原始文件，合计{sum(x["size"] for x in files):,} bytes', flush=True)
    receipt = {'dataset': REPOSITORY, 'revision': REVISION, 'started_at_utc': now(),
               'source': f'{BASE}/datasets/{REPOSITORY}', 'status': 'downloading',
               'expected_files': len(files), 'expected_bytes': sum(x['size'] for x in files), 'files': []}
    receipt_path = provenance / 'download-receipt.json'
    save_json(receipt_path, receipt)
    if args.verify_only:
        for entry in files:
            verified = matches(local_path(raw, entry['path']), entry)
            if not verified:
                raise ValueError('本地文件不存在或未通过发布方哈希校验：' + entry['path'])
            receipt['files'].append({'path': entry['path'], 'bytes': entry['size'], **verified})
    else:
        errors = []
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(download_one, raw, entry) for entry in sorted(files, key=lambda x: x['size'])]
            for future in as_completed(futures):
                try:
                    receipt['files'].append(future.result())
                except Exception as exc:
                    errors.append({'type': type(exc).__name__, 'message': str(exc)})
                    receipt['errors'] = errors
                save_json(receipt_path, receipt)
        if errors:
            receipt.update(status='partial_download', last_attempt_at_utc=now())
            save_json(receipt_path, receipt)
            raise RuntimeError('部分文件未下载完成；重跑命令可复用已验证文件并继续断点续传')
    receipt['status'] = 'all_files_downloaded_hashes_verified'
    receipt['files'].sort(key=lambda row: row['path'])
    save_json(receipt_path, receipt)
    for item in receipt['files']:
        path = local_path(raw, item['path'])
        if path.suffix == '.csv':
            item['csv'] = inspect_csv(path)
            print(f'完整CSV读取通过：{item["path"]}，{item["csv"]["rows"]:,}行', flush=True)
        else:
            container = inspect_container(path)
            if container:
                item['container'] = container
                print('容器完整性通过：' + item['path'], flush=True)
        save_json(receipt_path, receipt)
    receipt.update(status='passed', completed_at_utc=now(),
                   validation='逐文件发布方哈希+13个CSV完整解析+ZIP CRC+XZ全流完整性；未反序列化pickle',
                   business_integration='not_imported_into_olist_database')
    save_json(receipt_path, receipt)
    save_json(ROOT / 'reports/lade-download-validation.json', receipt)
    print(json.dumps({'status': receipt['status'], 'files': len(receipt['files']),
                      'bytes': sum(row['bytes'] for row in receipt['files']),
                      'csv_files': sum('csv' in row for row in receipt['files']),
                      'location': str(directory)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
