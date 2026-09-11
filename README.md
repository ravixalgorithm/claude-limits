# claude-limits

See the 5-hour and weekly limits of several Claude accounts on one shared page, whoever is using them and on whichever device.

A Claude Code plugin. Each person installs it once; from then on, whenever they use Claude Code, the logged-in account's limits show up on your group's page and in their own status line:

```
5h 84% left · week 98% left
```

The page is a secret GitHub gist with one card per account:

> ### 🟢 you@example.com
>
> | | Left | Resets |
> |:--|:--|:--|
> | **5-hour** | `████████░░` **84%** | Sat 12 Sep, 07:40 |
> | **Weekly** | `██████████` **98%** | Fri 18 Sep, 19:30 |
>
> <sub>Updated Sat 12 Sep, 02:48 · via MacBook</sub>

🟢 40% or more left · 🟡 15–40% · 🔴 under 15%. The colour follows whichever window is tighter.

## How it works

Claude Code gives its [status line](https://code.claude.com/docs/en/statusline) the same numbers `/usage` shows. The plugin sets itself up as your status line, prints them, and uploads them to the gist: when they change (at most once a minute), plus every 10 minutes.

- No scraping, no extra logins, and nobody's credentials leave their own machine. Only the numbers and the account email are uploaded.
- Switching accounts just works: it always reports whichever account is logged in.
- It reports while Claude Code is in use, which is when limits change. Otherwise the page shows the last reading and its reset time.

## Set up a page for your group (once)

1. **Create the page.** At [gist.github.com](https://gist.github.com), create a **secret** gist with any file (for example `README.md` saying "Claude limits"). Copy its ID, the last part of the URL.
2. **Create a token.** At [github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new), create a fine-grained token with **Account permissions → Gists: Read and write** and nothing else.
3. **Build the connection code:** `<gist id>:<token>`.
4. **Send everyone the install command** with your code filled in.

## Install (each person)

Paste into a terminal, all on one line (Command Prompt on Windows), then restart Claude Code:

```
claude plugin marketplace add https://github.com/ravixalgorithm/claude-limits.git && claude plugin install claude-limits@claude-limits --config "connection_code=<your group's code>"
```

Requires Python 3.9+ (preinstalled on macOS and Linux; on Windows install it from python.org or the Microsoft Store).

## Good to know

- **Privacy:** a secret gist is unlisted, not locked. Anyone with the link can view it, and anyone with the connection code can edit it. Share both only with your group.
- **Existing status line:** if you already have one, the plugin leaves it alone and doesn't share your limits.
- **Change the code:** `/plugin` → claude-limits → configure.
- **Uninstall:** `claude plugin uninstall claude-limits@claude-limits`, then remove the `statusLine` entry from `~/.claude/settings.json` and delete `~/.claude-limits.env`.
- **Problems:** upload errors are logged to `~/.claude-limits.log`.

## Development

Everything lives in one stdlib-only script, [`plugins/claude-limits/claude-limits.py`](plugins/claude-limits/claude-limits.py). Run its self-check with:

```
python3 plugins/claude-limits/claude-limits.py test
```

## License

[MIT](LICENSE)
