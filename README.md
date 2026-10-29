<p align="center">
  <img src="https://raw.githubusercontent.com/akshit9162/skillscout/main/docs/assets/header.svg" width="760" alt="skillscout: keep your agent's skills current, safely" />
</p>

<p align="center">
  <a href="https://akshit9162.github.io/skillscout">Website</a> &ensp;·&ensp;
  <a href="https://akshit9162.github.io/skillscout/report.html">Report: what's inside 6,726 skills</a> &ensp;·&ensp;
  <a href="#install">Install</a> &ensp;·&ensp;
  <a href="#a-week-with-skillscout">A week with it</a> &ensp;·&ensp;
  <a href="#how-it-works">How it works</a> &ensp;·&ensp;
  <a href="#what-gets-flagged">What gets flagged</a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT licence" /></a>
  <a href="https://github.com/akshit9162/skillscout/actions/workflows/ci.yml"><img src="https://github.com/akshit9162/skillscout/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/dependencies-0-brightgreen.svg" alt="No dependencies" />
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/akshit9162/skillscout/main/docs/assets/demo.svg" width="760" alt="Terminal demo: discover finds 216 recommended skills with 89 flagged high-risk; a high-risk skill is refused without --accept-risk; a low-risk one installs pinned to a commit" />
</p>

```bash
claude plugin marketplace add akshit9162/skillscout && claude plugin install skillscout@skillscout
# or the CLI on its own
pipx install skillscout-cli
```

AI tooling moves weekly. New skills, plugins and agent workflows land on GitHub every day, the ones you
installed keep changing upstream, and last month's best practice is already stale. Nobody has time to
watch hundreds of repos.

**skillscout keeps your agent current.** Once a week it checks the skill ecosystem against what *you*
work on, tells you what's new and worth having, and shows you exactly what changed in the skills you
already use.

Keeping up fast is only useful if it's safe. A skill is a set of instructions your agent follows
**with your permissions**, so skillscout scans every file for dangerous patterns, installs nothing without
your approval, pins each install to an exact commit, and re-scans every update before it can replace anything.

In one run over the public ecosystem it found **6,726 skills in 95 repos**. 198 had at least one high-risk flag,
mostly `sudo` and `curl | sh` install steps, plus prompt-injection-style text and hidden Unicode.
The [report](https://akshit9162.github.io/skillscout/report.html) reads every one of the scariest flags: they were defenders.

It's a zero-dependency Python CLI, and it also ships as a skill, so your agent can run the whole loop for you
and still ask before every install.

## A week with skillscout

Example session (counts vary week to week):

```text
Monday 10:00   skillscout discover --notify     (weekly, via schedule)
               → "4 new, 23 recommended, 11 flagged high-risk"
You            skillscout show github/awesome-copilot:eval-driven-dev
               → flags with file:line, the full SKILL.md, overlaps with what you have
You            skillscout approve eval-driven-dev   → copied, pinned to its commit
Later          skillscout updates               → upstream changed it; here's the diff
You            skillscout approve eval-driven-dev --update   → re-scanned, then replaced
```

## Install

### As a Claude Code plugin (the agent drives it)

```sh
claude plugin marketplace add akshit9162/skillscout
claude plugin install skillscout@skillscout
```

Then ask: *"find me skills for my Python and React work"*. The agent installs the CLI, runs the search,
summarises the report and shows you each skill's flags before asking to install it.

### As a CLI

```sh
pipx install skillscout-cli     # or: uv tool install skillscout-cli
skillscout init                                              # writes ~/.skillscout/profile.toml
```

Edit `[keywords]` in `~/.skillscout/profile.toml` with the words from your own work, then:

```sh
skillscout discover          # search, scan, score; writes ~/.skillscout/reports/<date>.md
skillscout list              # ranked; REC = recommended
skillscout show <id>         # every flag plus the full SKILL.md, before you decide
skillscout approve <id>      # copy into ~/.claude/skills and pin the commit
```

`<id>` is `owner/repo:skill-name`. The bare skill name works when it's unique.

## How it works

| Step | What happens |
|---|---|
| **Discover** | Runs your GitHub searches (topics like `claude-skills`, `agent-skills`) plus your trusted repos, then follows every repo linked from curated lists (your `lists`, plus any `awesome-*` repo it finds). Drops archived, stale (no push in a year), unpopular and huge repos. |
| **Clone** | Sparse, shallow clones: only folders that contain a `SKILL.md`, minus media and archives, 16 repos at a time. Repos with no push since the last run aren't fetched again. Repo code is never run. |
| **Scan** | Static checks on every file in the skill, reported with file and line ([below](#what-gets-flagged)). A skill whose folder (git tree hash) and the scanner's rules are both unchanged keeps its last result instead of being read again. |
| **Score** | 65% relevance to your keywords, 20% stars, 15% freshness, plus a bonus for trusted repos. |
| **Conflicts** | Compared against your `~/.claude/skills` and plugin skills: same name, or a description close enough that both would fire on the same prompt. |
| **Report** | What's new since your last run, recommended skills, and relevant ones that are blocked and why. |
| **Approve** | You pick one. It's copied (symlinks dropped) and pinned in `~/.skillscout/skills.lock.json` with its commit and a content hash. |
| **Update** | `skillscout updates` diffs each pinned skill against upstream and applies nothing. Taking an update re-runs the scan. |

## What gets flagged

| Level | Patterns |
|---|---|
| **High**, blocks install | Download piped into a shell · `sudo` · broad `rm -rf` · reading SSH keys or the keychain · decode-and-execute · uploading local files · hidden or direction-changing Unicode · "ignore previous instructions" · "don't tell the user" |
| **Medium** | Reading `.env` or API keys · force pushes · editing shell profiles or Claude settings · skipping permission prompts · hooks · scripts that make network calls · symlinks · binaries |
| **Low** | Other bundled scripts · no licence |

High-risk skills need `--accept-risk`, which the bundled skill tells the agent never to pass on its own.

## Safety rails

- Nothing installs without an explicit `approve`.
- `approve` never overwrites a folder skillscout didn't install, and never follows symlinks.
- Every install is pinned to a commit and a content hash.
- Updates are a diff first, and re-scanned before they replace anything. The test suite includes an upstream
  update that slips in `sudo rm -rf /`, and checks that re-approval refuses it.

## Every command

| Command | Does |
|---|---|
| `skillscout init` | Copy the example profile to `~/.skillscout/profile.toml` |
| `skillscout discover [--full] [--notify]` | Search, scan, score, write the report. `--full` ignores the caches; `--notify` sends a desktop notification (macOS, or Linux with `notify-send`) |
| `skillscout list [--all] [-n N]` | Ranked candidates |
| `skillscout show <id>` | Flags, conflicts and the full SKILL.md |
| `skillscout approve <id> [--update] [--accept-risk]` | Install and pin |
| `skillscout installed` · `skillscout remove <id>` | Pinned skills · uninstall |
| `skillscout updates` | Diff pinned skills against upstream |
| `skillscout schedule` | Set up weekly discovery: launchd on macOS, a systemd timer on Linux (cron line without systemd), a `schtasks` command on Windows. It prints the one command that turns it on |

Set `GITHUB_TOKEN`, or just be logged in with the GitHub CLI (`gh auth login`), for higher API rate limits (5,000 requests an hour instead of 60). Following lists costs one lookup per linked repo: up to 1,500 a run with a token, 40 without; links not yet checked go first, so later runs reach the rest. A token is optional: without it, a rate limit means waiting rather than failing. A weekly run with few upstream changes takes about 25 seconds; the first run takes a few minutes. Scheduled jobs don't see your shell's environment, so add the token to the job if you want it there.

## Limits

- The scanner catches known patterns. It can't prove a skill is safe, so read `show` before you approve.
- Relevance is keyword matching against your profile, not semantic search. Tune the weights.
- Every installed skill's description loads into every agent session. Keep the total to a few dozen.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

## License

MIT
