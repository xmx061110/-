"""LaDe独立SQLite导入：原始文本、可审查的任务时刻、质量标记与来源。"""
import argparse
from contextlib import contextmanager
import csv
from functools import lru_cache
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CITIES = {'jl': '吉林', 'hz': '杭州', 'sh': '上海', 'cq': '重庆', 'yt': '烟台'}
MONTH_DAYS = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
OFFSETS = [sum(MONTH_DAYS[:month]) for month in range(12)]
ELIGIBLE = '(quality_flags & 63)=0 AND duplicate_key=0'
FLAG_NAMES = {1: 'missing_order_id', 2: 'missing_courier_id', 4: 'invalid_accept_time',
              8: 'invalid_finish_time', 16: 'finish_before_accept', 32: 'invalid_pickup_window',
              64: 'ds_not_finish_day'}


def md_day(value):
    if not isinstance(value, str) or len(value) != 5 or value[2] != '-':
        return None
    try:
        month, day = int(value[:2]), int(value[3:])
    except ValueError:
        return None
    if not 1 <= month <= 12 or not 1 <= day <= MONTH_DAYS[month - 1]:
        return None
    return (OFFSETS[month - 1] + day - 1) * 86400


@lru_cache(maxsize=100000)
def time_seconds(value):
    if not isinstance(value, str) or len(value) != 14 or value[5] != ' ' or value[8] != ':' or value[11] != ':':
        return None
    base = md_day(value[:5])
    try:
        h, m, s = int(value[6:8]), int(value[9:11]), int(value[12:14])
    except ValueError:
        return None
    if base is None or not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 60):
        return None
    return base + h * 3600 + m * 60 + s


def day_label(value):
    return value[:5] if isinstance(value, str) and md_day(value[:5]) is not None else None


def present(value):
    return isinstance(value, str) and value.strip().lower() not in {'', 'nan', 'none', 'null'}


def normalized(task_id, kind, city, row):
    accept = time_seconds(row.get('accept_time'))
    finish_text = row.get('pickup_time' if kind == 'pickup' else 'delivery_time')
    finish = time_seconds(finish_text)
    start = time_seconds(row.get('time_window_start')) if kind == 'pickup' else None
    end = time_seconds(row.get('time_window_end')) if kind == 'pickup' else None
    flags = (0 if present(row.get('order_id')) else 1) | (0 if present(row.get('courier_id')) else 2)
    flags |= 4 if accept is None else 0
    flags |= 8 if finish is None else 0
    flags |= 16 if accept is not None and finish is not None and finish < accept else 0
    window_ok = start is not None and end is not None and start <= end
    flags |= 32 if kind == 'pickup' and not window_ok else 0
    ds = row.get('ds', '').zfill(4)
    ds_day = ds[:2] + '-' + ds[2:] if len(ds) == 4 else None
    flags |= 64 if ds_day != day_label(finish_text) else 0
    late = int(finish > end) if kind == 'pickup' and window_ok and finish is not None else None
    return (task_id, kind, city, row.get('order_id') if present(row.get('order_id')) else None,
            row.get('courier_id') if present(row.get('courier_id')) else None,
            day_label(row.get('accept_time')), day_label(finish_text), ds_day,
            accept, finish, start, end, flags, 0, late)


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


@contextmanager
def open_source(raw, entry):
    if entry.get('archive_member'):
        with zipfile.ZipFile(raw / entry['path']) as z:
            with z.open(entry['archive_member']) as stream:
                with io.TextIOWrapper(stream, encoding='utf-8-sig', newline='') as text:
                    yield text
    else:
        with (raw / entry['path']).open(encoding='utf-8-sig', newline='') as text:
            yield text


def build_database(raw, entries, destination, revision):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.stem + '.building.sqlite')
    if temporary.exists():
        temporary.unlink()  # 仅本构建器固定命名的临时库；不删除已发布库。
    con = sqlite3.connect(temporary)
    counts = []; columns_by_table = {}; next_task = 1
    try:
        con.executescript('''PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        PRAGMA foreign_keys=ON; PRAGMA cache_size=-65536; PRAGMA temp_store=FILE;
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE source_files(id INTEGER PRIMARY KEY,path TEXT NOT NULL,raw_table TEXT NOT NULL,
          kind TEXT,city_code TEXT,row_count INTEGER,bytes INTEGER,sha256 TEXT,columns_json TEXT,delimiter TEXT);
        CREATE TABLE tasks(task_id INTEGER PRIMARY KEY,kind TEXT NOT NULL,city_code TEXT NOT NULL,
          external_id TEXT,courier_id TEXT,accept_day TEXT,finish_day TEXT,dataset_day TEXT,
          accept_s INTEGER,finish_s INTEGER,window_start_s INTEGER,window_end_s INTEGER,
          quality_flags INTEGER NOT NULL,duplicate_key INTEGER NOT NULL,late_pickup INTEGER);
        ''')
        con.executemany('INSERT INTO metadata VALUES (?,?)', [
            ('revision', revision), ('status', 'building'),
            ('time_axis', 'year_unknown; relative 366-day month/day coordinate; no calendar year assigned'),
            ('time_visibility', 'Historical occurrence time only; actual ingestion delays not provided')])
        for file_id, entry in enumerate(entries, 1):
            path = entry['path']; kind = city = None
            if path.startswith(('pickup/', 'delivery/')):
                kind = path.split('/')[0]; city = Path(path).stem.split('_')[-1]
                if city not in CITIES:
                    raise ValueError('未知城市文件')
                table = 'raw_' + kind
            elif path == 'road-network/roads.csv': table = 'raw_roads'
            elif path == 'pickup_five_cities.csv': table = 'raw_pickup_bundle'
            elif path == 'delivery_five_cities.csv': table = 'raw_delivery_bundle'
            elif entry.get('archive_member'): table = 'raw_trajectory'
            else: raise ValueError('未识别结构化来源：' + path)
            if sha256(raw / path) != entry['sha256']:
                raise ValueError('原始文件哈希已变化：' + path)
            delimiter = '\t' if entry.get('csv', {}).get('delimiter') == 'tab' else ','
            label = path + ('::' + entry['archive_member'] if entry.get('archive_member') else '')
            with open_source(raw, entry) as stream:
                csv.field_size_limit(32 * 1024 * 1024)
                reader = csv.reader(stream, delimiter=delimiter, strict=True)
                columns = next(reader)
                if len(columns) != len(set(columns)) or set(columns) & {'_task_id', '_source_file_id', '_source_row'}:
                    raise ValueError('原始字段冲突')
                if table in columns_by_table and columns_by_table[table] != columns:
                    raise ValueError('同类城市CSV列结构不同')
                if table not in columns_by_table:
                    pk = '_task_id' if kind else '_row_id'
                    schema = ','.join(quote(x) + ' TEXT' for x in columns)
                    con.execute(f'CREATE TABLE {table}({pk} INTEGER PRIMARY KEY,_source_file_id INTEGER NOT NULL REFERENCES source_files(id),_source_row INTEGER NOT NULL,{schema})')
                    columns_by_table[table] = columns
                con.execute('INSERT INTO source_files VALUES (?,?,?,?,?,?,?,?,?,?)',
                            (file_id, label, table, kind, city, 0, entry['bytes'], entry['sha256'], json.dumps(columns), 'tab' if delimiter == '\t' else 'comma'))
                insert = f'INSERT INTO {table} VALUES ({",".join("?" for _ in range(len(columns)+3))})'
                batch = []; typed = []; row_count = 0
                for row_count, values in enumerate(reader, 1):
                    if len(values) != len(columns):
                        raise ValueError(f'行列数不匹配：{label}:{row_count}')
                    task_id = next_task if kind else None
                    batch.append((task_id, file_id, row_count, *values))
                    if kind:
                        typed.append(normalized(task_id, kind, city, dict(zip(columns, values))))
                        next_task += 1
                    if len(batch) >= 10000:
                        con.executemany(insert, batch)
                        if typed: con.executemany('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', typed)
                        batch.clear(); typed.clear()
                if batch:
                    con.executemany(insert, batch)
                    if typed: con.executemany('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', typed)
                expected = entry.get('csv', {}).get('rows')
                if expected is not None and row_count != expected:
                    raise ValueError('导入行数与已验证CSV不符')
                con.execute('UPDATE source_files SET row_count=? WHERE id=?', (row_count, file_id))
                count = con.execute(f'SELECT COUNT(*) FROM {table} WHERE _source_file_id=?', (file_id,)).fetchone()[0]
                if count != row_count: raise ValueError('数据库逐文件计数不一致')
                counts.append({'path': label, 'table': table, 'rows': row_count})
                con.commit()
                print(f'已导入：{label}，{row_count:,}行', flush=True)
        print('正在建立任务键、筛选和原始表复核索引……', flush=True)
        con.executescript('''
        CREATE INDEX tasks_key ON tasks(kind,city_code,external_id);
        CREATE INDEX tasks_scope ON tasks(kind,city_code,accept_day,courier_id,accept_s);
        CREATE TABLE duplicate_keys AS SELECT kind,city_code,external_id,COUNT(*) occurrences
          FROM tasks WHERE external_id IS NOT NULL GROUP BY kind,city_code,external_id HAVING COUNT(*)>1;
        CREATE UNIQUE INDEX duplicate_keys_key ON duplicate_keys(kind,city_code,external_id);
        ''')
        duplicate_groups = con.execute('SELECT COUNT(*) FROM duplicate_keys').fetchone()[0]
        if duplicate_groups:
            con.execute('''UPDATE tasks SET duplicate_key=1 WHERE task_id IN
              (SELECT t.task_id FROM duplicate_keys d JOIN tasks t
               ON t.kind=d.kind AND t.city_code=d.city_code AND t.external_id=d.external_id)''')
        for kind in ('pickup', 'delivery'):
            if 'raw_' + kind in columns_by_table:
                con.execute(f'CREATE INDEX raw_{kind}_scope ON raw_{kind}(_source_file_id,substr(accept_time,1,5),courier_id)')
                con.execute(f'CREATE INDEX raw_{kind}_key ON raw_{kind}(_source_file_id,order_id)')
        con.execute(f'''CREATE TABLE scope_catalog AS SELECT kind,city_code,accept_day,courier_id,
          COUNT(*) valid_tasks,MIN(accept_s) first_accept_s,MAX(accept_s) last_accept_s
          FROM tasks WHERE {ELIGIBLE} AND accept_day IS NOT NULL
          GROUP BY kind,city_code,accept_day,courier_id''')
        con.execute('CREATE UNIQUE INDEX scope_catalog_key ON scope_catalog(kind,city_code,accept_day,courier_id)')
        flags = dict(con.execute('SELECT quality_flags,COUNT(*) FROM tasks GROUP BY quality_flags'))
        groups = [dict(zip(['kind', 'rows', 'eligible', 'duplicate_rows'], row)) for row in con.execute(
            f'SELECT kind,COUNT(*),SUM(CASE WHEN {ELIGIBLE} THEN 1 ELSE 0 END),SUM(duplicate_key) FROM tasks GROUP BY kind')]
        if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok': raise ValueError('SQLite完整性检查未通过')
        if con.execute('PRAGMA foreign_key_check').fetchone() is not None: raise ValueError('来源外键检查未通过')
        con.execute("UPDATE metadata SET value='passed' WHERE key='status'")
        con.commit()
    except Exception:
        con.close()
        raise
    con.close()
    temporary.replace(destination)
    return {'status': 'passed', 'revision': revision, 'database': str(destination), 'database_bytes': destination.stat().st_size,
            'source_counts': counts, 'task_groups': groups, 'duplicate_key_groups': duplicate_groups,
            'quality_flag_combinations': flags, 'flag_meanings': FLAG_NAMES,
            'limitations': ['年份未提供，采用年内相对时刻，不赋予日历年份。',
                            '同一子集/城市/外部编号重复的记录保留原文，暂排除于有效任务与算法候选。',
                            '顶层bundle与城市明细分表，禁止直接追加计数；pickle仍为归档，不反序列化。']}


def main():
    receipt = json.loads((ROOT / 'reports/lade-download-validation.json').read_text(encoding='utf8'))
    if receipt['status'] != 'passed': raise ValueError('请先完成下载验证')
    entries = [entry for entry in receipt['files'] if 'csv' in entry]
    archive = dict(next(entry for entry in receipt['files'] if entry['path'] == 'courier_detailed_trajectory.zip'))
    archive['archive_member'] = 'courier_detailed_trajectory.csv'
    entries.append(archive)
    result = build_database(ROOT / 'data/lade/raw', entries, ROOT / 'data/lade/warehouse.sqlite', receipt['revision'])
    (ROOT / 'reports/lade-import-validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({k: v for k, v in result.items() if k in {'status', 'database', 'database_bytes', 'task_groups', 'duplicate_key_groups'}}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
