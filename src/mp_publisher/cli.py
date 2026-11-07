from __future__ import annotations

import argparse
import json
import sys

import httpx

from . import config
from .channels import check_wechat_credentials
from .errors import PublisherError, SkipArticle
from .legacy import import_file
from .operations import backup_state, inspect_state
from .profiles import PROFILES, default_period, get_profile, scheduled_periods, validate_period
from .runner import read_article, run
from .state import StateStore


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Fail-closed Notion to WeChat draft publisher")
    root.add_argument("--env-file")
    root.add_argument("--state")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("preview", "dry-run", "run"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--profile", required=True, choices=sorted(PROFILES))
        cmd.add_argument("--period")
    status = commands.add_parser("status")
    status.add_argument("--profile", choices=sorted(PROFILES))
    health = commands.add_parser("health")
    health.add_argument("--profile", choices=sorted(PROFILES))
    commands.add_parser("check-wechat", help="Read-only WeChat token and IP whitelist check")
    backup = commands.add_parser("backup-state")
    backup.add_argument("--output", required=True)
    backup.add_argument("--authorize", action="store_true")
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--source-key", required=True)
    reconcile.add_argument("--channel", required=True, choices=("wechat",))
    reconcile.add_argument("--result", required=True, choices=("SUCCESS", "UNKNOWN", "ABSENT"))
    reconcile.add_argument("--receipt")
    reconcile.add_argument("--evidence", help="Private operator evidence for verified-absent draft")
    reconcile.add_argument("--confirm-absent", action="store_true")
    reconcile.add_argument("--authorize", action="store_true")
    backfill = commands.add_parser("backfill")
    backfill.add_argument("--profile", required=True, choices=sorted(PROFILES))
    backfill.add_argument("--period", required=True)
    backfill.add_argument("--authorize", action="store_true")
    legacy = commands.add_parser("import-history")
    legacy.add_argument("--file", required=True)
    legacy.add_argument("--authorize", action="store_true")
    return root


def _period(args: argparse.Namespace):
    profile = get_profile(args.profile)
    if profile.name == "overseas" and not args.period:
        raise PublisherError("Overseas stories require explicit --period; no automatic date selection")
    period = args.period or default_period(profile)
    return profile, validate_period(profile, period)


def execute(args: argparse.Namespace) -> dict[str, object]:
    config.load_environment(args.env_file)
    store = StateStore(args.state or config.state_path())
    if args.command == "check-wechat":
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            check_wechat_credentials(app_id=config.required("WECHAT_APP_ID"),
                                     app_secret=config.required("WECHAT_APP_SECRET"), client=client)
        return {"status": "OK", "scope": "wechat_credential_and_ip_check", "external_write": False}
    if args.command in {"preview", "dry-run", "run", "backfill"}:
        profile, period = _period(args)
    if args.command in {"preview", "dry-run"}:
        article = read_article(profile, period)
        result = article.public_summary()
        result["status"] = "PREVIEW_ONLY" if args.command == "preview" else "DRY_RUN_OK"
        result["schedule"] = profile.schedule
        return result
    if args.command == "run":
        # An explicitly chosen --period is always a single report. During the
        # scheduled first-Monday TrustMRR invocation, attempt both weekly and
        # monthly independently; missing/not-READY monthly content is expected.
        if args.period is not None or profile.name != "trustmrr":
            outcome = run(profile, period, store)
            return {**outcome.article.public_summary(), "status": "COMPLETED", "wechat": outcome.wechat}
        outcomes: list[dict[str, object]] = []
        failures = 0
        for scheduled_period in scheduled_periods(profile):
            try:
                result = run(profile, scheduled_period, store)
                outcomes.append({"period": scheduled_period, "status": "COMPLETED",
                                 "source_key": result.article.source_key,
                                 "wechat": result.wechat})
            except SkipArticle as exc:
                outcomes.append({"period": scheduled_period, "status": "SKIPPED",
                                 "reason": str(exc)})
            except PublisherError as exc:
                failures += 1
                outcomes.append({"period": scheduled_period, "status": "FAILED",
                                 "reason": str(exc)})
        complete = any(item["status"] == "COMPLETED" for item in outcomes)
        status = "FAILED" if failures else ("COMPLETED" if complete else "SKIPPED")
        return {"profile": profile.name, "status": status, "reports": outcomes}
    if args.command == "backfill":
        if not args.authorize:
            raise PublisherError("backfill requires --authorize")
        outcome = run(profile, period, store)
        return {**outcome.article.public_summary(), "status": "BACKFILL_COMPLETED", "wechat": outcome.wechat}
    if args.command == "status":
        return {"status": "OK", "wechat_deliveries": store.rows(args.profile)}
    if args.command == "health":
        return inspect_state(store, args.profile)
    if args.command == "backup-state":
        if not args.authorize:
            raise PublisherError("backup-state requires --authorize")
        return backup_state(store, args.output)
    if args.command == "import-history":
        if not args.authorize:
            raise PublisherError("import-history requires --authorize")
        return {"status": "IMPORTED", "rows": import_file(store, args.file)}
    if args.command == "reconcile":
        if not args.authorize:
            raise PublisherError("reconcile requires --authorize")
        if args.result == "ABSENT":
            if not args.confirm_absent or not args.evidence:
                raise PublisherError("ABSENT requires --confirm-absent and --evidence")
            store.rearm_verified_absent(args.source_key, args.channel, args.evidence)
            return {"status": "REARMED", "source_key": args.source_key,
                    "channel": args.channel, "result": "PENDING", "external_write": False}
        receipt = args.receipt
        detail = "manual reconciliation"
        if args.result == "SUCCESS" and not receipt:
            raise PublisherError("WeChat SUCCESS reconciliation requires --receipt")
        store.mark(args.source_key, args.channel, args.result, receipt=receipt, detail=detail)
        return {"status": "RECONCILED", "source_key": args.source_key,
                "channel": args.channel, "result": args.result}
    raise PublisherError("Unknown command")


def main() -> None:
    try:
        result = execute(parser().parse_args())
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        if result.get("status") in {"FAILED", "ACTION_REQUIRED"}:
            raise SystemExit(2)
    except SkipArticle as exc:
        print(json.dumps({"status": "SKIPPED", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(0)
    except (PublisherError, OSError, ValueError) as exc:
        print(json.dumps({"status": "FAILED", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
