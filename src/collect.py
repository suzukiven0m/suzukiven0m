#!/usr/bin/env python3
"""Snapshot a GitHub account's contribution calendar into JSON.

One GraphQL call, 365 days. No dependency beyond the standard library.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

API = "https://api.github.com/graphql"

QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
            contributionLevel
          }
        }
      }
    }
  }
}
"""

LEVELS = {
    "NONE": 0,
    "FIRST_QUARTILE": 1,
    "SECOND_QUARTILE": 2,
    "THIRD_QUARTILE": 3,
    "FOURTH_QUARTILE": 4,
}


def token() -> str:
    """A token from the environment, else the one `gh` is already logged in with."""
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        if value := os.environ.get(name):
            return value
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(
            "no token: set GH_TOKEN, or install and log in to the GitHub CLI"
        ) from error
    return result.stdout.strip()


def fetch(login: str, tok: str, today: date) -> dict:
    body = json.dumps(
        {
            "query": QUERY,
            "variables": {
                "login": login,
                "from": (today - timedelta(days=364)).isoformat() + "T00:00:00Z",
                "to": today.isoformat() + "T23:59:59Z",
            },
        }
    ).encode()
    if urllib.parse.urlsplit(API).scheme != "https":
        raise SystemExit(f"refusing to send a token to {API}")
    request = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": f"bearer {tok}",
            "Content-Type": "application/json",
            "User-Agent": "gif-github-collect",
        },
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 - scheme checked above
            request, timeout=60
        ) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:500]
        raise SystemExit(f"github returned {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise SystemExit(f"could not reach github: {error.reason}") from error

    if errors := payload.get("errors"):
        raise SystemExit(f"graphql: {errors[0].get('message', errors)}")
    return payload["data"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", required=True, help="account to snapshot")
    parser.add_argument("--out", required=True, help="path to write the JSON snapshot")
    parser.add_argument("--today", default=None, help="override the date (YYYY-MM-DD)")
    args = parser.parse_args()

    try:
        today = date.fromisoformat(args.today) if args.today else date.today()
    except ValueError as error:
        raise SystemExit(f"--today {args.today!r} is not YYYY-MM-DD") from error
    data = fetch(args.login, token(), today)
    if not data or not data.get("user"):
        raise SystemExit(f"no such user: {args.login}")

    calendar = data["user"]["contributionsCollection"]["contributionCalendar"]
    days = [
        {
            "date": day["date"],
            "count": day["contributionCount"],
            "level": LEVELS.get(day["contributionLevel"], 0),
        }
        for week in calendar["weeks"]
        for day in week["contributionDays"]
    ]

    # A blank graph would silently publish an empty GIF, so refuse instead.
    if not days:
        raise SystemExit("the calendar came back empty; refusing to snapshot")

    yesterday = (today - timedelta(days=1)).isoformat()
    snapshot = {
        "login": args.login,
        "generated": today.isoformat(),
        "total": calendar["totalContributions"],
        "yesterday": next((d for d in days if d["date"] == yesterday), None),
        "days": days,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=1) + "\n")
    print(f"{out}: {len(days)} days, {snapshot['total']} contributions", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
