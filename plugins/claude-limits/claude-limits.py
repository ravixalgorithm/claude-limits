#!/usr/bin/env python3
"""claude-limits: every account's Claude Code limits on one shared page (a GitHub gist).

Claude Code hands its status line the same 5-hour and weekly numbers `/usage` shows. This
script is that status line: it prints them, and uploads them to the group's gist as one file
per account, so everyone sees every account no matter who is using it or on which device.

Modes:
  statusline  run by Claude Code as the status line (reads its JSON on stdin)
  connect     run by the plugin's SessionStart hook: saves the connection code, sets the status line
  test        self-check
Config (env or ~/.claude-limits.env): CLAUDE_LIMITS_GIST, CLAUDE_LIMITS_TOKEN.
"""
import json, os, re, shutil, socket, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
CONFIG = HOME / ".claude-limits.env"
STATE = HOME / ".claude-limits-state.json"
LOG = HOME / ".claude-limits.log"
WINDOWS = (("5-hour", "5h", "five_hour"), ("Weekly", "week", "seven_day"))


def config(key, path=CONFIG):
    """Env first, else the config file, read directly so the token never sits in Claude Code's env."""
    if os.environ.get(key):
        return os.environ[key]
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            k, _, v = line.strip().removeprefix("export ").partition("=")
            if k == key:
                return v.strip().strip("'\"")
    except OSError:
        pass
    raise KeyError(f"{key} is not set (env or {path})")


def device():
    return os.environ.get("CLAUDE_LIMITS_NAME") or socket.gethostname().removesuffix(".local")


def claude_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")


def active_email():
    # Claude Code keeps the logged-in account in ~/.claude.json, or inside CLAUDE_CONFIG_DIR when set.
    p = Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json" if os.environ.get("CLAUDE_CONFIG_DIR") else HOME / ".claude.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))["oauthAccount"]["emailAddress"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def limits(rate_limits):
    """The status line's rate_limits → [(label, short label, % left, reset time or None)]."""
    out = []
    for label, short, key in WINDOWS:
        w = rate_limits.get(key) or {}
        if w.get("used_percentage") is not None:
            reset = w.get("resets_at")
            out.append((label, short, 100 - float(w["used_percentage"]),
                        datetime.fromtimestamp(reset, timezone.utc).astimezone() if reset else None))
    return out


def dot(left):
    return "🟢" if left >= 40 else "🟡" if left >= 15 else "🔴"


def row(label, left, reset):
    n = max(0, min(10, round(left / 10)))
    when = f"{reset:%a %d %b, %H:%M}" if reset else "—"
    return f"| **{label}** | `{'█' * n}{'░' * (10 - n)}` **{left:.0f}%** | {when} |\n"


def card(email, windows, device, now):
    # The tighter window sets the colour: an account is only as usable as its lower limit.
    return (f"### {dot(min(left for _, _, left, _ in windows))} {email}\n\n"
            "| | Left | Resets |\n|:--|:--|:--|\n"
            + "".join(row(label, left, reset) for label, _, left, reset in windows)
            + f"\n<sub>Updated {now:%a %d %b, %H:%M} · via {device}</sub>\n")


def filename(email):
    return re.sub(r"[^\w.-]", "-", email) + ".md"


def statusline_text(info):
    windows = limits(info.get("rate_limits") or {})
    return (" · ".join(f"{short} {left:.0f}% left" for _, short, left, _ in windows)
            or (info.get("model") or {}).get("display_name", ""))


def push(files, timeout=5):
    req = urllib.request.Request(
        f"https://api.github.com/gists/{config('CLAUDE_LIMITS_GIST')}",
        data=json.dumps({"files": files}).encode(),
        method="PATCH",
        headers={"Authorization": f"Bearer {config('CLAUDE_LIMITS_TOKEN')}",
                 "Accept": "application/vnd.github+json"})
    urllib.request.urlopen(req, timeout=timeout).close()


def statusline():
    info = json.loads(sys.stdin.buffer.read() or b"{}")
    print(statusline_text(info), flush=True)  # print first: the upload must never delay or break the bar
    windows, email = limits(info.get("rate_limits") or {}), active_email()
    if not windows or not email:
        return
    key = [email] + [round(left) for _, _, left, _ in windows]
    try:
        last = json.loads(STATE.read_text())
    except (OSError, ValueError):
        last = {}
    age = time.time() - last.get("ts", 0)
    # This runs on every UI update: upload when the numbers or account change (at most once a
    # minute), plus every 10 minutes as a heartbeat. State is written first so parallel
    # sessions don't all upload the same reading.
    if age < 60 or (key == last.get("key") and age < 600):
        return
    STATE.write_text(json.dumps({"key": key, "ts": time.time()}))
    try:
        push({filename(email): {"content": card(email, windows, device(), datetime.now())}})
    except Exception as e:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%d %b %H:%M} upload failed: {e}\n")


def connect():
    """SessionStart hook: save the plugin's connection code and point the status line at a stable copy."""
    code = os.environ.get("CLAUDE_PLUGIN_OPTION_CONNECTION_CODE", "").strip()
    gist, _, token = code.partition(":")
    if not gist or not token:
        print("claude-limits: no connection code; set it with /plugin → claude-limits → configure", file=sys.stderr)
        return
    wanted = f"CLAUDE_LIMITS_GIST={gist}\nCLAUDE_LIMITS_TOKEN={token}\n"
    if not CONFIG.exists() or CONFIG.read_text(encoding="utf-8") != wanted:
        CONFIG.write_text(wanted, encoding="utf-8")
        CONFIG.chmod(0o600)
    # A stable copy, because the plugin's own folder moves on every plugin update.
    dest = claude_dir() / "claude-limits.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if Path(__file__).resolve() != dest.resolve():
        shutil.copyfile(__file__, dest)
    settings = claude_dir() / "settings.json"
    s = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
    command = f'"{Path(sys.executable).as_posix()}" "{dest.as_posix()}" statusline'
    current = (s.get("statusLine") or {}).get("command", "")
    if current == command:
        return
    if current and "claude-limits.py" not in current:
        # ponytail: never replace someone's own status line; chain it in if people ask for both
        print("claude-limits: you already have a status line, so limits aren't being shared", file=sys.stderr)
        return
    s["statusLine"] = {"type": "command", "command": command}
    settings.write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")


def test():
    rl = {"five_hour": {"used_percentage": 90, "resets_at": 4070908800}, "seven_day": {"used_percentage": 10}}
    w = limits(rl)
    assert [(label, short, left) for label, short, left, _ in w] == [("5-hour", "5h", 10.0), ("Weekly", "week", 90.0)], w
    assert w[0][3] == datetime(2099, 1, 1, tzinfo=timezone.utc) and w[1][3] is None, w
    c = card("a@x.com", w, "Mac", datetime(2026, 9, 11, 14, 5))
    assert c.startswith("### 🔴 a@x.com"), c  # the tighter window sets the colour
    assert f"| **5-hour** | `█░░░░░░░░░` **10%** | {w[0][3]:%a %d %b, %H:%M} |" in c, c
    assert "| **Weekly** | `█████████░` **90%** | — |" in c and "Fri 11 Sep, 14:05 · via Mac" in c, c
    assert statusline_text({"rate_limits": rl}) == "5h 10% left · week 90% left"
    assert statusline_text({"model": {"display_name": "Opus"}}) == "Opus"
    assert limits({}) == [] and limits({"five_hour": {}}) == []
    assert filename("a+b@x.com") == "a-b-x.com.md"

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "env"
        p.write_text("export CLAUDE_LIMITS_GIST='abc'\nCLAUDE_LIMITS_TOKEN=tok=1\n")
        assert config("CLAUDE_LIMITS_GIST", p) == "abc" and config("CLAUDE_LIMITS_TOKEN", p) == "tok=1"
        try:
            config("CLAUDE_LIMITS_NOPE", p)
            raise AssertionError("missing key must raise")
        except KeyError:
            pass
    print("ok")


if __name__ == "__main__":
    {"statusline": statusline, "connect": connect, "test": test}.get(
        sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
