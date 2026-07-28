from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .pipeline import notify, process
from .rendering import rebuild_indexes
from .state import load_state


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="YouTube 财经知识库")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("process", help="发现、抓取并分析字幕")
    run.add_argument("--config", type=Path, default=Path("config/channels.yaml"))
    run.add_argument("--state", type=Path, default=Path("state/videos.json"))
    run.add_argument("--channel")
    run.add_argument("--video")
    run.add_argument("--backfill-days", type=int)
    run.add_argument("--preview", action="store_true")
    run.add_argument("--force", action="store_true")
    run.add_argument(
        "--fail-on-errors",
        action="store_true",
        help="处理完成并保存状态后，在任一视频失败时返回非零状态",
    )

    deliver = subparsers.add_parser("notify", help="补发待处理通知，不调用 AI")
    deliver.add_argument("--state", type=Path, default=Path("state/videos.json"))
    deliver.add_argument("--repository-url", default=os.environ.get("REPOSITORY_URL", ""))
    deliver.add_argument("--branch", default=os.environ.get("GITHUB_REF_NAME", "main"))

    rebuild = subparsers.add_parser("rebuild-indexes", help="确定性重建索引，不调用 AI")
    rebuild.add_argument("--state", type=Path, default=Path("state/videos.json"))
    return parser


def main() -> None:
    args = _parser().parse_args()
    root = args.root.resolve()
    if args.command == "process":
        output = process(
            root,
            config_path=(root / args.config).resolve(),
            state_path=(root / args.state).resolve(),
            channel_filter=args.channel or None,
            video_url=args.video or None,
            backfill_days=args.backfill_days,
            preview=args.preview,
            force=args.force,
        )
        print(json.dumps(output.__dict__, ensure_ascii=False))
        if args.fail_on_errors and output.failed:
            raise SystemExit(1)
    elif args.command == "notify":
        if not args.repository_url:
            raise SystemExit("--repository-url or REPOSITORY_URL is required")
        output = notify(root, (root / args.state).resolve(), args.repository_url, args.branch)
        print(json.dumps(output, ensure_ascii=False))
    else:
        state_path = (root / args.state).resolve()
        rebuild_indexes(root, load_state(state_path)["videos"])
        print('{"indexes":"rebuilt"}')


if __name__ == "__main__":
    main()
