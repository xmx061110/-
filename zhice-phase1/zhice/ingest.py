"""只从完整 ZIP 提取预期的九个 CSV，保留原始字节及来源证据。"""
import hashlib
import json
import shutil
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .schema import DOWNLOAD_URL, FILES, SOURCE_URL


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".zip.part")
    try:
        with urllib.request.urlopen(DOWNLOAD_URL, timeout=60) as response, temporary.open("wb") as out:
            shutil.copyfileobj(response, out)
        if not zipfile.is_zipfile(temporary):
            raise ValueError("下载内容不是完整 ZIP，可能是登录页面")
        temporary.replace(destination)
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise RuntimeError("无法公开下载。请在浏览器打开 Kaggle 数据集页面，必要时登录，"
                           "下载完整 ZIP 后运行 acquire --archive <ZIP路径>。"
                           "不要在终端或聊天中提供账号口令。") from exc
    return destination


def acquire(root, archive=None, official_list=None):
    root = Path(root)
    raw = root / "data/raw"
    provenance = root / "data/provenance"
    raw.mkdir(parents=True, exist_ok=True)
    provenance.mkdir(parents=True, exist_ok=True)
    mode = "existing_local_archive" if archive else "official_public_download"
    archive = Path(archive) if archive else download(root / "data/olist-original.zip")
    expected_sizes = None
    if official_list:
        meta = json.loads(Path(official_list).read_text(encoding="utf-8-sig"))
        if meta.get("errorMessage") or meta.get("nextPageToken"):
            raise ValueError("官方文件清单有错误或分页未完成")
        expected_sizes = {f["name"]: int(f["totalBytes"]) for f in meta["datasetFiles"]}
        if set(expected_sizes) != set(FILES.values()):
            raise ValueError("官方清单与九表契约不一致")
    records = []
    with zipfile.ZipFile(archive) as z:
        members = [i for i in z.infolist() if not i.is_dir()]
        if len(members) != 9 or {i.filename for i in members} != set(FILES.values()):
            raise ValueError("需九个原始 CSV 的完整根目录 ZIP；请勿传入抽样或派生数据")
        bad = z.testzip()
        if bad:
            raise ValueError(f"ZIP CRC 错误：{bad}")
        for info in sorted(members, key=lambda x: x.filename):
            if expected_sizes and info.file_size != expected_sizes[info.filename]:
                raise ValueError(f"官方文件大小不符：{info.filename}")
            target = raw / info.filename
            content = z.read(info)
            if target.exists() and target.read_bytes() != content:
                raise ValueError(f"原始目录已有不同版本文件，拒绝覆盖：{target}")
            target.write_bytes(content)
            records.append({"file": info.filename, "bytes": len(content),
                            "sha256": sha256(target), "zip_crc": f"{info.CRC:08x}"})
    if official_list:
        (provenance / "official-files.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "source_page": SOURCE_URL, "official_download_endpoint": DOWNLOAD_URL,
        "source_mode": mode, "archive_name": archive.name,
        "archive_bytes": archive.stat().st_size, "archive_sha256": sha256(archive),
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "license_label_on_source": "CC BY-NC-SA 4.0",
        "official_size_check": expected_sizes is not None, "zip_crc_passed": True,
        "files": records,
    }
    (provenance / "source-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest
