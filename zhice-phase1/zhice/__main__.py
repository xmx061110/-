import argparse
import csv
import json
from pathlib import Path

from .ingest import acquire
from .metrics import get_metrics
from .pipeline import build
from .schema import FILES


def main():
    parser = argparse.ArgumentParser(description="智策第一阶段 Olist 数据处理与指标查询")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("acquire", "all"):
        sub = commands.add_parser(name)
        sub.add_argument("--archive", type=Path)
        sub.add_argument("--official-list", type=Path)
    commands.add_parser("build")
    commands.add_parser("verify")
    commands.add_parser("report")
    commands.add_parser("reproduce")
    metrics = commands.add_parser("metrics")
    metrics.add_argument("--month")
    metrics.add_argument("--category")
    metrics.add_argument("--seller")
    metrics.add_argument("--group-by", choices=["none","month","category","seller"], default="none")
    metrics.add_argument("--min-orders", type=int, default=0)
    metrics.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command in ("acquire", "all"):
        # 'all' can reproduce offline from the already delivered raw CSVs.
        if args.command == "acquire" or args.archive or not all((root/"data/raw"/f).exists() for f in FILES.values()):
            manifest = acquire(root,args.archive,args.official_list)
            print(f"verified {len(manifest['files'])} original CSVs")
    if args.command in ("build","all"):
        profile = build(root)
        print(f"input fingerprint: {profile['input_fingerprint']}")
    if args.command in ("verify","all"):
        from .verify import verify
        print(json.dumps(verify(root),ensure_ascii=False,indent=2))
    if args.command in ("report","all"):
        from .reporting import generate_reports
        generate_reports(root)
        print(f"reports: {root/'reports'}")
    if args.command == "reproduce":
        from .reproduce import reproduce
        print(json.dumps(reproduce(root),ensure_ascii=False,indent=2))
    if args.command == "metrics":
        rows = get_metrics(root/"data/warehouse.sqlite", month=args.month, category=args.category,
                           seller=args.seller, group_by=args.group_by, min_orders=args.min_orders)
        output = json.dumps(rows,ensure_ascii=False,indent=2,allow_nan=False)
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            if args.output.suffix.lower()==".csv":
                with args.output.open("w",encoding="utf-8-sig",newline="") as stream:
                    if rows:
                        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
            else:
                args.output.write_text(output+"\n",encoding="utf-8")
        print(output)


if __name__ == "__main__":
    main()
