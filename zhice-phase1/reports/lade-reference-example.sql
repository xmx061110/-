-- 2000仅为月日计算参考，不表示真实年份。
WITH base AS MATERIALIZED (
     SELECT CASE WHEN length(i.accept_time)=14 AND strftime('%m-%d %H:%M:%S',julianday('2000-'||i.accept_time))=i.accept_time
      THEN CAST(ROUND((julianday('2000-'||i.accept_time)-julianday('2000-01-01'))*86400) AS INTEGER) ELSE NULL END accept_s,CASE WHEN length(i.pickup_time)=14 AND strftime('%m-%d %H:%M:%S',julianday('2000-'||i.pickup_time))=i.pickup_time
      THEN CAST(ROUND((julianday('2000-'||i.pickup_time)-julianday('2000-01-01'))*86400) AS INTEGER) ELSE NULL END finish_s,
     CASE WHEN length(i.time_window_start)=14 AND strftime('%m-%d %H:%M:%S',julianday('2000-'||i.time_window_start))=i.time_window_start
      THEN CAST(ROUND((julianday('2000-'||i.time_window_start)-julianday('2000-01-01'))*86400) AS INTEGER) ELSE NULL END window_start_s,CASE WHEN length(i.time_window_end)=14 AND strftime('%m-%d %H:%M:%S',julianday('2000-'||i.time_window_end))=i.time_window_end
      THEN CAST(ROUND((julianday('2000-'||i.time_window_end)-julianday('2000-01-01'))*86400) AS INTEGER) ELSE NULL END window_end_s,
     (lower(trim(i.order_id)) NOT IN ('','nan','none','null') AND lower(trim(i.courier_id)) NOT IN ('','nan','none','null')) ids_valid,
     CASE WHEN lower(trim(i.order_id)) NOT IN ('','nan','none','null') AND
       (SELECT COUNT(*) FROM raw_pickup j WHERE j._source_file_id=i._source_file_id AND j.order_id=i.order_id)>1
       THEN 1 ELSE 0 END duplicate_key
     FROM raw_pickup i WHERE i._source_file_id=9 AND substr(i.accept_time,1,5)='05-01'
     AND ('14171' IS NULL OR i.courier_id='14171')),
     checked AS (SELECT *,CASE WHEN ids_valid AND accept_s IS NOT NULL AND finish_s IS NOT NULL AND finish_s>=accept_s
       AND window_start_s IS NOT NULL AND window_end_s IS NOT NULL AND window_end_s>=window_start_s THEN 0 ELSE 1 END invalid_record FROM base),
     scoped AS (SELECT *,CASE WHEN invalid_record=0 AND duplicate_key=0 THEN 1 ELSE 0 END eligible FROM checked)
     SELECT COUNT(*) observations,
 COALESCE(SUM(eligible),0) eligible,
 COALESCE(SUM(duplicate_key),0) duplicate_observations,
 COALESCE(SUM(invalid_record),0) invalid_observations,
 COALESCE(SUM(CASE WHEN eligible AND 'pickup'='pickup' THEN 1 ELSE 0 END),0) window_eligible,
 COALESCE(SUM(CASE WHEN eligible AND 'pickup'='pickup' AND finish_s>window_end_s THEN 1 ELSE 0 END),0) late_count,
 COALESCE(SUM(CASE WHEN eligible AND 'pickup'='pickup' AND finish_s<window_start_s THEN 1 ELSE 0 END),0) early_count,
 COALESCE(SUM(CASE WHEN eligible THEN finish_s-accept_s ELSE 0 END),0) duration_total_s FROM scoped;