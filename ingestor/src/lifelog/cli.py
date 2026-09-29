import argparse
import getpass

from lifelog.config import Settings
from lifelog.service import sync_once
from lifelog.sources.timetagger import TimeTaggerClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lifelog")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("sync", help="run one sync now")
    get_token = commands.add_parser("get-token", help="print a TimeTagger API token for .env")
    get_token.add_argument("--username", required=True)
    args = parser.parse_args(argv)

    settings = Settings.from_env()

    if args.command == "sync":
        result = sync_once(settings, trigger="cli", wait_for_lock=True)
        detail = f" ({result.error})" if result.error else ""
        print(f"{result.status}: {result.n_records} records{detail}")
        return 1 if result.status == "failed" else 0

    password = getpass.getpass("TimeTagger password: ")
    with TimeTaggerClient(settings.timetagger_api_url, token="") as client:
        print(client.get_api_token(client.login(args.username, password)))
    return 0
