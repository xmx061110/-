"""LaDe范围目录与独立原始表SQL复核，不把月日伪装为已知年份。"""
from contextlib import closing
from datetime import datetime
from functools import lru_cache
import sqlite3
import re

from .lade_db import CITIES, ELIGIBLE, md_day


def connect(database):
    con = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    if con.execute("SELECT value FROM metadata WHERE key='status'").fetchone()[0] != 'passed':
        con.close(); raise ValueError('LaDe数据库尚未完成导入验证')
    return con


@lru_cache(maxsize=20000)
def raw_time(value):
    if not isinstance(value, str) or len(value) != 14:
        return None
    try:
        parsed = datetime.strptime('2000-' + value, '%Y-%m-%d %H:%M:%S')
        if parsed.strftime('%m-%d %H:%M:%S') != value: return None
        return int((parsed - datetime(2000, 1, 1)).total_seconds())
    except ValueError:
        return None


def scope(kind, city, day, courier_id=None):
    if not isinstance(kind, str) or not isinstance(city, str) or kind not in ('pickup', 'delivery') or city not in CITIES:
        raise ValueError('请选择有效子集与城市')
    if not isinstance(day, str) or md_day(day) is None:
        raise ValueError('日期须为数据中的MM-DD；未提供年份')
    if courier_id is not None and (not isinstance(courier_id, str) or not courier_id or len(courier_id) > 128):
        raise ValueError('快递员编号不合法')
    return {'kind': kind, 'city': city, 'day': day, 'courier': courier_id}


def catalog(database, city='jl', day=None, kind='pickup'):
    if not isinstance(city, str) or not isinstance(kind, str) or city not in CITIES or kind not in ('pickup', 'delivery'):
        raise ValueError('城市或子集不合法')
    with closing(connect(database)) as con:
        days = [r[0] for r in con.execute('SELECT DISTINCT accept_day FROM scope_catalog WHERE kind=? AND city_code=? ORDER BY accept_day', (kind, city))]
        selected = day or (days[0] if days else None)
        if selected is not None and selected not in days: raise ValueError('日期不在所选城市的有效任务中')
        couriers = [dict(r) for r in con.execute('''SELECT courier_id,valid_tasks,first_accept_s,last_accept_s
            FROM scope_catalog WHERE kind=? AND city_code=? AND accept_day=?
            ORDER BY valid_tasks DESC,courier_id LIMIT 1000''', (kind, city, selected))]
        for row in couriers:
            minutes = min(1435, (row['first_accept_s'] % 86400 + 299) // 300 * 5)
            row['start_time'] = f'{minutes//60:02d}:{minutes%60:02d}'
        counts = [dict(r) for r in con.execute('SELECT kind,city_code,row_count FROM source_files WHERE kind IS NOT NULL')]
    return {'cities': [{'value': key, 'label': label} for key, label in CITIES.items()],
            'city': city, 'kind': kind, 'days': days, 'day': selected, 'couriers': couriers,
            'source_counts': counts, 'date_semantics': '月-日，年份未知；仅供同一数据快照回放'}


SUMS = '''COUNT(*) observations,
 COALESCE(SUM(eligible),0) eligible,
 COALESCE(SUM(duplicate_key),0) duplicate_observations,
 COALESCE(SUM(invalid_record),0) invalid_observations,
 COALESCE(SUM(CASE WHEN eligible AND :kind='pickup' THEN 1 ELSE 0 END),0) window_eligible,
 COALESCE(SUM(CASE WHEN eligible AND :kind='pickup' AND finish_s>window_end_s THEN 1 ELSE 0 END),0) late_count,
 COALESCE(SUM(CASE WHEN eligible AND :kind='pickup' AND finish_s<window_start_s THEN 1 ELSE 0 END),0) early_count,
 COALESCE(SUM(CASE WHEN eligible THEN finish_s-accept_s ELSE 0 END),0) duration_total_s'''

METRIC_SQL = f'''WITH scoped AS (
 SELECT *,CASE WHEN {ELIGIBLE} THEN 1 ELSE 0 END eligible,
 CASE WHEN (quality_flags & 63)!=0 THEN 1 ELSE 0 END invalid_record
 FROM tasks WHERE kind=:kind AND city_code=:city AND accept_day=:day
 AND (:courier IS NULL OR courier_id=:courier)) SELECT {SUMS} FROM scoped'''


def reference_sql(kind):
    finish = 'pickup_time' if kind == 'pickup' else 'delivery_time'
    a, b = (sql_time('i.time_window_start'), sql_time('i.time_window_end')) if kind == 'pickup' else ('NULL','NULL')
    valid_window = 'AND window_start_s IS NOT NULL AND window_end_s IS NOT NULL AND window_end_s>=window_start_s' if kind == 'pickup' else ''
    return f'''WITH base AS MATERIALIZED (
     SELECT {sql_time('i.accept_time')} accept_s,{sql_time('i.'+finish)} finish_s,
     {a} window_start_s,{b} window_end_s,
     (lower(trim(i.order_id)) NOT IN ('','nan','none','null') AND lower(trim(i.courier_id)) NOT IN ('','nan','none','null')) ids_valid,
     CASE WHEN lower(trim(i.order_id)) NOT IN ('','nan','none','null') AND
       (SELECT COUNT(*) FROM raw_{kind} j WHERE j._source_file_id=i._source_file_id AND j.order_id=i.order_id)>1
       THEN 1 ELSE 0 END duplicate_key
     FROM raw_{kind} i WHERE i._source_file_id=:file_id AND substr(i.accept_time,1,5)=:day
     AND (:courier IS NULL OR i.courier_id=:courier)),
     checked AS (SELECT *,CASE WHEN ids_valid AND accept_s IS NOT NULL AND finish_s IS NOT NULL AND finish_s>=accept_s
       {valid_window} THEN 0 ELSE 1 END invalid_record FROM base),
     scoped AS (SELECT *,CASE WHEN invalid_record=0 AND duplicate_key=0 THEN 1 ELSE 0 END eligible FROM checked)
     SELECT {SUMS} FROM scoped'''


def sql_time(column):
    stamp = f"julianday('2000-'||{column})"
    return f"""CASE WHEN length({column})=14 AND strftime('%m-%d %H:%M:%S',{stamp})={column}
      THEN CAST(ROUND(({stamp}-julianday('2000-01-01'))*86400) AS INTEGER) ELSE NULL END"""


def standalone_sql(sql, params):
    def literal(value):
        if value is None: return 'NULL'
        if isinstance(value, int): return str(value)
        return "'" + str(value).replace("'", "''") + "'"
    return '-- 2000仅为月日计算参考，不表示真实年份。\n' + re.sub(
        r':(kind|city|day|courier|file_id)\b', lambda match: literal(params[match[1]]), sql) + ';'


def verified_metrics(database, kind, city, day, courier_id=None):
    params = scope(kind, city, day, courier_id)
    with closing(connect(database)) as con:
        file_row = con.execute('SELECT id FROM source_files WHERE kind=? AND city_code=?', (kind, city)).fetchone()
        if file_row is None: raise ValueError('缺少所选城市原始表')
        params['file_id'] = file_row[0]
        calculated = dict(con.execute(METRIC_SQL, params).fetchone())
        reference = dict(con.execute(reference_sql(kind), params).fetchone())
        if calculated != reference: raise ValueError('LaDe指标与原始表独立复核不一致，拒绝输出')
    denominator = calculated['window_eligible']
    return {'status': 'passed', 'filters': {k: params[k] for k in ('kind', 'city', 'day', 'courier')},
            'metrics': {**calculated,
                        'pickup_late_count': calculated['late_count'] if kind == 'pickup' else None,
                        'pickup_late_rate': calculated['late_count'] / denominator if denominator else None,
                        'mean_accept_to_finish_minutes': calculated['duration_total_s'] / calculated['eligible'] / 60 if calculated['eligible'] else None},
            'reference_counts': reference, 'sql': {'normalized': METRIC_SQL, 'raw_reference': reference_sql(kind),
                'standalone_raw_reference': standalone_sql(reference_sql(kind), params), 'parameters': params},
            'limitations': ['按接单月日统计有效完整历史记录，年份未知。',
                            '重复任务键和关键时间异常不进入有效分母；原文保留。',
                            '派送子集没有已核实的承诺截止时间，不输出派送延迟率。']}
