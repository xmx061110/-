"""在独立临时数据库重建同一数据，核对数据库字节和输入指纹。"""
import tempfile
from pathlib import Path

from .ingest import sha256
from .pipeline import build, write_json


def reproduce(root):
    root=Path(root).resolve()
    database=root/"data/warehouse.sqlite"
    original_hash=sha256(database)
    temp_root=root/"work"
    temp_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="reproduce-",dir=temp_root) as directory:
        directory=Path(directory).resolve()
        assert directory.is_relative_to(temp_root.resolve())
        replay_db=directory/"warehouse.sqlite"
        profile=build(root,database=replay_db)
        replay_hash=sha256(replay_db)
        if replay_hash!=original_hash:
            raise AssertionError("相同输入在本环境重建的数据库 SHA-256 不一致")
    result={"status":"passed","method":"independent full rebuild, exact SQLite file SHA-256 equality in this runtime",
            "database_sha256":original_hash,"rebuild_database_sha256":replay_hash,
            "input_fingerprint":profile["input_fingerprint"],
            "note":"同一 Python/Pandas/SQLite 环境中核验字节级可复现；跨版本应比较语义结果，不要求 SQLite 文件字节相同。"}
    write_json(root/"reports/reproducibility.json",result)
    return result
