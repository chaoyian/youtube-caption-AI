from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .notifications import normalize_email_recipients, send_optimization_preview, test_email
from .pipeline import notify, process
from .prompt_optimizer import (
    DEFAULT_CRITERIA,
    DEFAULT_OBJECTIVE,
    continue_session,
    finalize_session,
    load_session,
    runtime_from_environment,
    session_summary,
    start_session,
)
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
    run.add_argument(
        "--latest-per-channel",
        type=int,
        help="每个选中频道只处理最新的 N 条视频",
    )
    run.add_argument("--preview", action="store_true")
    run.add_argument(
        "--prompt-eval-only",
        action="store_true",
        help="只抓取字幕并生成提示词评测案例；必须与 --preview 一起使用",
    )
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

    email_test = subparsers.add_parser("test-email", help="发送最新一篇笔记作为测试，不调用 AI")
    email_test.add_argument("--state", type=Path, default=Path("state/videos.json"))
    email_test.add_argument("--repository-url", default=os.environ.get("REPOSITORY_URL", ""))
    email_test.add_argument("--branch", default=os.environ.get("GITHUB_REF_NAME", "main"))
    email_test.add_argument(
        "--to",
        action="append",
        default=[],
        help="测试收件地址，可重复或使用英文逗号分隔；留空沿用 EMAIL_TO",
    )

    optimizer = subparsers.add_parser("prompt-optimize", help="评估并优化金融笔记质量提示词")
    optimizer_actions = optimizer.add_subparsers(dest="optimizer_action", required=True)

    optimizer_start = optimizer_actions.add_parser("start", help="建立会话并运行第一轮")
    optimizer_start.add_argument("--case", action="append", type=Path, required=True)
    optimizer_start.add_argument("--objective", default=DEFAULT_OBJECTIVE)
    optimizer_start.add_argument("--criteria", default=DEFAULT_CRITERIA)
    optimizer_start.add_argument("--model", default=os.environ.get("POE_MODEL", "GPT-5.4"))
    optimizer_start.add_argument("--rounds", type=int, default=5)
    optimizer_start.add_argument(
        "--point-limit",
        type=int,
        default=int(os.environ.get("POE_OPTIMIZER_POINT_LIMIT", "50000")),
    )
    optimizer_start.add_argument("--email-to", action="append", default=[])
    optimizer_start.add_argument("--json", action="store_true")

    optimizer_continue = optimizer_actions.add_parser("continue", help="提交选择/反馈并运行下一轮")
    optimizer_continue.add_argument("session", type=Path)
    continue_parent = optimizer_continue.add_mutually_exclusive_group()
    continue_parent.add_argument("--select", choices=["A", "B", "C", "a", "b", "c"])
    continue_parent.add_argument("--keep", action="store_true")
    optimizer_continue.add_argument("--feedback", default="")
    optimizer_continue.add_argument("--edit-file", type=Path)
    optimizer_continue.add_argument("--email-to", action="append", default=[])
    optimizer_continue.add_argument("--json", action="store_true")

    optimizer_finalize = optimizer_actions.add_parser("finalize", help="明确选择并更新正式提示词")
    optimizer_finalize.add_argument("session", type=Path)
    finalize_choice = optimizer_finalize.add_mutually_exclusive_group(required=True)
    finalize_choice.add_argument("--select", choices=["A", "B", "C", "a", "b", "c"])
    finalize_choice.add_argument("--keep", action="store_true")
    optimizer_finalize.add_argument("--json", action="store_true")

    rebuild = subparsers.add_parser("rebuild-indexes", help="确定性重建索引，不调用 AI")
    rebuild.add_argument("--state", type=Path, default=Path("state/videos.json"))
    return parser


def _resolved_session(path: Path) -> Path:
    resolved = path.resolve()
    return resolved / "session.json" if resolved.is_dir() else resolved


def _print_optimizer_summary(summary: dict, session_path: Path, as_json: bool) -> None:
    payload = {"session_path": str(session_path), **summary}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
        return
    print(f"会话：{session_path}")
    print(
        f"第 {summary['round']}/{summary['max_rounds']} 轮；"
        f"已用 {summary['points_spent']}/{summary['point_limit']} Poe 点"
    )
    for choice, candidate in summary["choices"].items():
        print(
            f"\n[{choice}] {candidate['title']} — {candidate['score']:.2f}/100\n"
            f"策略：{candidate['strategy']}\n"
            f"优势：{candidate['strongest'] or '未记录'}\n"
            f"风险：{candidate['risk'] or '未记录'}\n"
            f"证据：{candidate['rationale'] or '未记录'}\n"
            f"提示词：\n{candidate['prompt']}\n"
            "样例输出：\n"
            + json.dumps(candidate["outputs"], ensure_ascii=False, indent=2)[:6000]
        )
    print("机器建议：" + summary["machine_recommendation"])
    if summary["plateau"]:
        print("提示：连续两轮提升不明显，建议定稿或更换评测案例。")


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
            latest_per_channel=args.latest_per_channel,
            preview=args.preview,
            prompt_eval_only=args.prompt_eval_only,
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
    elif args.command == "test-email":
        if not args.repository_url:
            raise SystemExit("--repository-url or REPOSITORY_URL is required")
        records = load_state((root / args.state).resolve())["videos"]
        recipients = normalize_email_recipients(args.to) if args.to else None
        note_path = test_email(root, records, args.repository_url, args.branch, recipients)
        print(json.dumps({"email_test": "sent", "note_path": note_path}, ensure_ascii=False))
    elif args.command == "prompt-optimize":
        if args.optimizer_action == "start":
            runtime = runtime_from_environment(model=args.model, point_limit=args.point_limit)
            session_path, state = start_session(
                root,
                [(root / path).resolve() if not path.is_absolute() else path for path in args.case],
                runtime,
                objective=args.objective,
                criteria=args.criteria,
                max_rounds=args.rounds,
                point_limit=args.point_limit,
            )
        elif args.optimizer_action == "continue":
            session_path = _resolved_session(args.session)
            previous = load_session(session_path)
            runtime = runtime_from_environment(previous)
            edited_prompt = args.edit_file.read_text(encoding="utf-8") if args.edit_file else None
            state = continue_session(
                root,
                session_path,
                runtime,
                choice=args.select,
                keep=args.keep,
                feedback=args.feedback,
                edited_prompt=edited_prompt,
            )
        else:
            session_path = _resolved_session(args.session)
            prompt_dir, state = finalize_session(
                root, session_path, choice=args.select, keep=args.keep
            )
            summary = session_summary(state)
            payload = {"prompt_dir": str(prompt_dir), **summary}
            if args.json:
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print(f"已定稿：{prompt_dir}")
                print(f"选择：{state['winner']['id']}，分数 {state['winner']['score']:.2f}/100")
            return
        summary = session_summary(state)
        recipients = normalize_email_recipients(args.email_to) if args.email_to else []
        if recipients:
            send_optimization_preview(summary, recipients)
        _print_optimizer_summary(summary, session_path, args.json)
    else:
        state_path = (root / args.state).resolve()
        rebuild_indexes(root, load_state(state_path)["videos"])
        print('{"indexes":"rebuilt"}')


if __name__ == "__main__":
    main()
