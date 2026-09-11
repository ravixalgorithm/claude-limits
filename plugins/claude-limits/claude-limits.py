#!/usr/bin/env python3
"""Share Claude limits in one GitHub gist, one file per account.

Modes:
  statusline  Claude Code status line: shows this account's limits and uploads them (the plugin sets this up)
  connect     plugin SessionStart hook: saves the connection code, points the status line here
  (none)      claude-swap mode: report every account registered with `cswap add` (scheduled job)
  test        self-check
Config (env or ~/.claude-limits.env): CLAUDE_LIMITS_GIST, CLAUDE_LIMITS_TOKEN.
"""
import json, os, re, shutil, socket, subprocess, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
CONFIG = HOME / ".claude-limits.env"
STATE = HOME / ".claude-limits-state.json"
LOG = HOME / ".claude-limits.log"
# cron / Task Scheduler PATH usually lacks ~/.local/bin, where uv installs cswap
CSWAP = shutil.which("cswap") or str(HOME / ".local/bin/cswap")
# under pythonw on Windows, cswap would otherwise flash a console every run
QUIET = {"capture_output": True, "text": True, "timeout": 60,
         "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


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


def usage():
    # Re-adding an existing account only refreshes its stored login, so this is safe every run.
    subprocess.run([CSWAP, "add"], stdin=subprocess.DEVNULL, **QUIET)
    return json.loads(subprocess.run([CSWAP, "list", "--json"], **QUIET).stdout)


def local(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()


def dot(left):
    return "🟢" if left >= 40 else "🟡" if left >= 15 else "🔴"


def row(label, w):
    left = 100 - w["pct"]
    n = max(0, min(10, round(left / 10)))
    reset = f"{local(w['resetsAt']):%a %d %b, %H:%M}" if w.get("resetsAt") else "—"
    return f"| **{label}** | `{'█' * n}{'░' * (10 - n)}` **{left:.0f}%** | {reset} |"


def card(a, device, when):
    u = a["usage"]
    main = [(label, u[k]) for label, k in (("5-hour", "fiveHour"), ("Weekly", "sevenDay")) if u.get(k)]
    scoped = [(f"Weekly · {s['name']}", s) for s in u.get("scoped") or [] if s.get("name")]
    # Heading colour follows the account-wide windows; a spent per-model limit doesn't block the account.
    worst = min((100 - w["pct"] for _, w in main), default=0)
    return (f"### {dot(worst)} {a.get('alias') or a['email']}\n\n"
            "| | Left | Resets |\n|:--|:--|:--|\n"
            + "".join(row(label, w) + "\n" for label, w in main + scoped)
            + f"\n<sub>Updated {when:%a %d %b, %H:%M} · via {device}</sub>\n")


def files(data, device, now):
    out = {}
    for a in data.get("accounts", []):
        # Only push fresh readings; a stale or logged-out copy must not overwrite
        # a fresher one another device pushed. The gist keeps the last good file.
        if a.get("usageStatus") != "ok" or not a.get("usage"):
            continue
        when = local(a["usageFetchedAt"]) if a.get("usageFetchedAt") else now
        out[re.sub(r"[^\w.-]", "-", a["email"]) + ".md"] = {"content": card(a, device, when)}
    return out


def push(gist_files, timeout=30):
    req = urllib.request.Request(
        f"https://api.github.com/gists/{config('CLAUDE_LIMITS_GIST')}",
        data=json.dumps({"files": gist_files}).encode(),
        method="PATCH",
        headers={"Authorization": f"Bearer {config('CLAUDE_LIMITS_TOKEN')}",
                 "Accept": "application/vnd.github+json"})
    urllib.request.urlopen(req, timeout=timeout).close()


# --- Claude Code status line -------------------------------------------------------------

def claude_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")


def active_email():
    # Claude Code keeps the logged-in account in ~/.claude.json, or inside CLAUDE_CONFIG_DIR when set.
    p = Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json" if os.environ.get("CLAUDE_CONFIG_DIR") else HOME / ".claude.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))["oauthAccount"]["emailAddress"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def from_statusline(rl, email):
    """Shape the status line's rate_limits like a claude-swap account row, so files() renders both."""
    usage = {}
    for key, src in (("fiveHour", "five_hour"), ("sevenDay", "seven_day")):
        w = rl.get(src)
        if w and w.get("used_percentage") is not None:
            reset = w.get("resets_at")
            usage[key] = {"pct": float(w["used_percentage"]),
                          "resetsAt": datetime.fromtimestamp(reset, timezone.utc).isoformat() if reset else None}
    return {"email": email, "usageStatus": "ok", "usage": usage}


def statusline_text(info):
    rl = info.get("rate_limits") or {}
    parts = [f"{label} {100 - rl[k]['used_percentage']:.0f}% left"
             for label, k in (("5h", "five_hour"), ("week", "seven_day"))
             if (rl.get(k) or {}).get("used_percentage") is not None]
    return " · ".join(parts) or (info.get("model") or {}).get("display_name", "")


def statusline():
    info = json.loads(sys.stdin.buffer.read() or b"{}")
    print(statusline_text(info), flush=True)  # print first: the upload must never delay or break the bar
    email, acct = active_email(), from_statusline(info.get("rate_limits") or {}, None)
    if not email or not acct["usage"]:
        return
    acct["email"] = email
    key = [email] + [round(w["pct"]) for w in acct["usage"].values()]
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
        push(files({"accounts": [acct]}, device(), datetime.now()), timeout=5)
    except Exception as e:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%d %b %H:%M} statusline upload failed: {e}\n")


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
    reset = "2099-01-01T00:00:00Z"
    sample = {"schemaVersion": 1, "accounts": [
        {"email": "a@x.com", "usageStatus": "ok", "usageFetchedAt": "2026-09-11T08:35:00Z", "usage": {
            "fiveHour": {"pct": 90.0, "resetsAt": reset}, "sevenDay": {"pct": 10.0},
            "scoped": [{"pct": 100.0, "name": "Fable"}]}},
        {"email": "b@x.com", "usageStatus": "token_expired", "usage": None,
         "lastGoodUsage": {"fiveHour": {"pct": 25.0, "resetsAt": reset}}},
        {"email": "c@x.com", "alias": "work", "usageStatus": "ok", "usage": {"fiveHour": {"pct": 50.0}}},
    ]}
    out = files(sample, "Mac", datetime(2026, 9, 11, 14, 5))
    assert sorted(out) == ["a-x.com.md", "c-x.com.md"], out  # expired account never overwrites
    a, c = out["a-x.com.md"]["content"], out["c-x.com.md"]["content"]
    assert a.startswith("### 🔴 a@x.com"), a  # worst account-wide window decides the colour
    assert f"| **5-hour** | `█░░░░░░░░░` **10%** | {local(reset):%a %d %b, %H:%M} |" in a, a
    assert "| **Weekly** | `█████████░` **90%** | — |" in a, a
    assert "| **Weekly · Fable** | `░░░░░░░░░░` **0%** | — |" in a, a
    assert local("2026-09-11T08:35:00Z").strftime("%a %d %b, %H:%M") + " · via Mac" in a, a
    assert c.startswith("### 🟢 work") and "**50%**" in c and "Fri 11 Sep, 14:05 · via Mac" in c, c
    assert files({"error": {"kind": "x"}}, "Mac", datetime.now()) == {}

    # status line: Claude Code's rate_limits → same card as claude-swap data
    rl = {"five_hour": {"used_percentage": 23.5, "resets_at": 4070908800}, "seven_day": {"used_percentage": 60}}
    acct = from_statusline(rl, "d@x.com")
    assert acct["usage"]["fiveHour"] == {"pct": 23.5, "resetsAt": "2099-01-01T00:00:00+00:00"}, acct
    assert acct["usage"]["sevenDay"] == {"pct": 60.0, "resetsAt": None}, acct
    d = files({"accounts": [acct]}, "PC", datetime(2026, 9, 12, 9, 0))["d-x.com.md"]["content"]
    assert d.startswith("### 🟢 d@x.com") and "**76%**" in d and "**40%**" in d and "via PC" in d, d
    assert statusline_text({"rate_limits": rl}) == "5h 76% left · week 40% left"
    assert statusline_text({"model": {"display_name": "Opus"}}) == "Opus"
    assert from_statusline({}, "e@x.com")["usage"] == {}

    # config file parsing
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
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "test":
        test()
    elif mode == "statusline":
        statusline()
    elif mode == "connect":
        connect()
    else:
        # ponytail: a cswap failure only reaches the log, not the gist; add an error file if that bites
        data = usage()
        gist_files = files(data, device(), datetime.now())
        if gist_files:
            push(gist_files)
        skipped = [f"{a.get('email')}={a.get('usageStatus')}" for a in data.get("accounts", [])
                   if a.get("usageStatus") != "ok"]
        print(f"{datetime.now():%d %b %H:%M} pushed {len(gist_files)}"
              + (f", skipped {', '.join(skipped)}" if skipped else "")
              + (f", cswap error {data['error']}" if "error" in data else ""), flush=True)
