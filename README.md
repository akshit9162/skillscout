<p align="center">
  <img src="https://akshit9162.github.io/skillscout/assets/header.svg" width="760" alt="skillscout: keep your agent's skills current, for every kind of work you do" />
</p>

<p align="center">
  <a href="https://akshit9162.github.io/skillscout">Website</a> &ensp;·&ensp;
  <a href="https://akshit9162.github.io/skillscout/report.html">Report: what's inside 6,726 skills</a> &ensp;·&ensp;
  <a href="#install">Install</a> &ensp;·&ensp;
  <a href="#personas">Personas</a> &ensp;·&ensp;
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
  <img src="https://akshit9162.github.io/skillscout/assets/demo.svg" width="760" alt="Terminal demo: init drafts keywords from one line; persona add creates a brand persona; discover scores 23,865 skills with a section for the brand persona; persona build writes its agent; claude --agent brand starts it" />
</p>

```bash
claude plugin marketplace add akshit9162/skillscout && claude plugin install skillscout@skillscout
# or the CLI on its own
pipx install skillscout-cli
```

AI tooling moves weekly. New skills, plugins and agent workflows land on GitHub every day, the ones you
installed keep changing upstream, and last month's best practice is already stale. Nobody has time to
watch hundreds of repos.

**skillscout keeps your agent current, for each kind of work you do.** Every week a shared index of every
public skill it can find (32,360 from 1,173 repos at the last build) is rebuilt. skillscout scores it on your
machine against what *you* work on, tells you what's new and worth having, and shows you exactly what
changed in the skills you already use.

You probably don't do one kind of work. Coding on Monday, a landing page on Tuesday, investor emails on
Friday. So skillscout keeps **personas**: one per kind of work, each with its own search and its own Claude
Code agent, loaded with the best skills for that work and kept up to date as better ones appear.

Keeping up fast is only useful if it's safe. A skill is a set of instructions your agent follows
**with your permissions**, so skillscout scans every file for dangerous patterns, installs nothing without
your approval, pins each install to an exact commit, and re-scans every update before it can replace anything.

In a hand-checked run on 27 Oct 2026 over **6,726 skills in 95 repos**, 198 had at least one high-risk flag,
mostly `sudo` and `curl | sh` install steps, plus prompt-injection-style text and hidden Unicode.
The [report](https://akshit9162.github.io/skillscout/report.html) reads every one of the scariest flags: they were defenders.

It's a zero-dependency Python CLI, and it also ships as a skill, so your agent can run the whole loop for you
and still ask before every install.

## Personas

A persona is one side of your work, in your words. skillscout doesn't ship any: yours might be "builder",
"brand" and "outreach", someone else's "trading" and "thesis".

```text
You     "set up personas for me"           (in Claude Code, with the plugin)
Agent   reads your recent sessions on this machine, with your OK (skillscout history)
        → proposes: builder · brand · outreach, each with the sessions it came from
You     keep brand and builder, rename outreach to launches
Agent   skillscout persona add brand --about "D2C marketing: ad copy, landing pages, Instagram reels"
        → keywords drafted from terms skills actually use: instagram reels, ad copy, landing pages...
        skillscout discover                → a "For your brand work" section in the report
        skillscout persona build brand     → ~/.claude/agents/brand.md, skills: copy, landing-page-design, script...
You     claude --agent brand
```

### Switch persona, and the rest stays out of the way

Every installed skill's description loads into every Claude Code session, so a big collection crowds out
the work. Switch to the persona you're in and Claude Code lists only its skills:

```sh
skillscout persona use brand            # or say "switch to brand" to the agent
#  active: brand
#    hid 31: fastapi, postgres-patterns, seo-audit, ...
#    plugins on 1: marketing@knowledge-work-plugins
#    plugins off 2: sales@knowledge-work-plugins, android-skills@android-skills
skillscout persona use brand builder    # several at once
skillscout persona start brand          # switch, then start Claude Code (with brand's agent if built)
skillscout persona use --core           # no persona: only [switch] core and unclaimed skills
skillscout persona use --clear          # everything back as it was
```

It writes `skillOverrides` and `enabledPlugins` in `~/.claude/settings.json`: nothing moves on disk.
A persona claims the skills that fit its keywords, plus any you pin with `skills = [...]`, minus `exclude`.
Plugins switch whole, so you pin them with `plugins = [...]` (`persona add` drafts them from the plugins
whose skills fit). skillscout restores only what it changed, never overwrites an override you set, and a
skill you approve later joins whichever personas it fits. A new session picks the switch up; in an open
one, run `/reload-skills` and `/reload-plugins`.

From then on, each weekly report has a section per persona, and `persona build` swaps in a better skill when
one arrives (after you've read and approved it). `persona build` only ever rewrites the agent's `skills:` list:
its prompt, tone, tools, model and memory are yours.

The CLI works without the agent too:

```sh
skillscout persona add brand --about "D2C marketing: ad copy, landing pages, Instagram reels"
skillscout persona list            # keywords, best installed skills, what's worth installing
skillscout persona build --all     # write or update ~/.claude/agents/<name>.md
```

**Your history stays on your machine.** `skillscout history` reads `~/.claude/projects` only when you run it,
keeps only what you typed (no tool output, no pasted blocks, no links) and sends nothing anywhere. Grouping
sessions into personas is left to you or the agent in your own session: counting words can't tell short chat
messages apart reliably, and a wrong persona is worse than none.

## A week with skillscout

Example session (counts vary week to week):

```text
Monday 10:00   skillscout discover --notify     (weekly, via schedule)
               → "23 recommended for you (4 new this run), 11 relevant but flagged high-risk"
               → plus a section per persona: "For your brand work", "For your builder work"
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

Then ask: *"find me skills for my Python and React work"*, or *"set up personas for me"*. The agent installs the CLI, runs the search,
summarises the report and shows you each skill's flags before asking to install it.

### As a CLI

```sh
pipx install skillscout-cli     # or: uv tool install skillscout-cli
skillscout init --about "backend in Python, code review, RL evals, Postgres"
```

`init` writes `~/.skillscout/profile.toml` and drafts `[keywords]` from your line, using only terms that skills
actually use. Check them, then:

```sh
skillscout discover          # score this week's index; writes ~/.skillscout/reports/<date>.md
skillscout list              # ranked; REC = recommended
skillscout show <id>         # every flag plus the full SKILL.md, before you decide
skillscout approve <id>      # copy into ~/.claude/skills and pin the commit
```

`<id>` is `owner/repo:skill-name`. The bare skill name works when it's unique. If one repo has two skills with the same name, the second is `owner/repo:path/to/skill`.

## How it works

The crawl is the same for everyone, so it runs once a week in this repo's [index workflow](.github/workflows/index.yml) and is published as `index.json.gz`. `skillscout discover` downloads that one file (seconds, no GitHub token, your keywords never leave your machine) and scores it locally. `show` and `approve` fetch the skill and scan it again on your machine, so nothing is installed on the index's word. `discover --local` runs the whole crawl yourself instead.

Each published index carries a signed [build attestation](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations): proof that this repo's index workflow built that exact file. Check one with `gh attestation verify ~/.skillscout/index.json.gz --repo akshit9162/skillscout`, or set `verify = true` under `[index]` and `discover` checks every download and refuses one that fails.

| Step | What happens |
|---|---|
| **Discover** (weekly job, or `--local`) | Runs your GitHub searches (topics like `claude-skills`, `agent-skills`) plus your trusted repos, then follows every repo linked from curated lists (your `lists`, plus any `awesome-*` repo it finds). Drops archived, stale (no push in a year), unpopular and huge repos. |
| **Clone** | Sparse, shallow clones: only folders that contain a `SKILL.md`, minus media and archives, 16 repos at a time. Repos with no push since the last run aren't fetched again. Repo code is never run. |
| **Scan** | Static checks on every file in the skill, reported with file and line ([below](#what-gets-flagged)). A skill whose folder (git tree hash) and the scanner's rules are both unchanged keeps its last result instead of being read again. |
| **Audit** (weekly job) | Asks [skills.sh](https://skills.sh) for its Gen, Socket and Snyk audits of each skill and stores them in the index. Your machine never calls skills.sh ([below](#second-opinion-from-skillssh)). |
| **Score** (your machine) | 65% relevance to your keywords (phrases match in order, singular or plural), 20% stars, 15% freshness, plus a bonus for trusted repos. |
| **Conflicts** | Compared against your `~/.claude/skills` and plugin skills: same name, or a description close enough that both would fire on the same prompt. |
| **Report** | What's new since your last run, recommended skills, and relevant ones that are blocked and why. |
| **Approve** | You pick one. It's fetched and scanned again on your machine (that scan decides), then copied (symlinks dropped) and pinned in `~/.skillscout/skills.lock.json` with its commit and a content hash. |
| **Update** | `skillscout updates` diffs each pinned skill against upstream and applies nothing. Taking an update re-runs the scan, builds the new copy beside the old one and swaps them; if any step fails, the old copy and its pin stay. `skillscout rollback <id>` puts back the version an update replaced. |

## What gets flagged

| Level | Patterns |
|---|---|
| **High**, blocks install | Download piped into a shell · `sudo` · broad `rm -rf` · reading SSH keys or the keychain · decode-and-execute · uploading local files · hidden or direction-changing Unicode · "ignore previous instructions" · "don't tell the user" |
| **Medium** | Reading `.env` or API keys · force pushes · editing shell profiles or Claude settings · skipping permission prompts · hooks · scripts that make network calls · symlinks · binaries |
| **Low** | Other bundled scripts · no licence |

High-risk skills need `--accept-risk`, which the bundled skill tells the agent never to pass on its own.

### Second opinion from skills.sh

[skills.sh](https://skills.sh) runs three outside audits (Gen, Socket and Snyk) and shows them before
`npx skills add`. skillscout's weekly job fetches the same audits and puts them in the signed index, so the
report and `show` list them next to skillscout's own flags, and your machine makes no extra calls.

A skill that two or more auditors rate high or critical is blocked like a high-risk flag (`--accept-risk`
to override). One auditor alone is shown as a note: they disagree often. On the 8 Nov 2026 index, skills.sh
had audits for 21,253 of 32,360 skills; 384 had two or more severe ratings, and only 100 of those were also
high under skillscout's rules. The two catch different things, which is the point of having both.

## Safety rails

- Nothing installs without an explicit `approve`.
- `approve` never overwrites a folder skillscout didn't install, and never follows symlinks.
- A skill can't name its way out of the skills folder: a `name:` like `../../x` falls back to the folder's own name, and a path that leads outside its repo is refused. The install name comes from the `SKILL.md` fetched on your machine, not from the index.
- Every install is pinned to a commit and a content hash.
- Updates are a diff first, and re-scanned before they replace anything. The test suite includes an upstream
  update that slips in `sudo rm -rf /`, and checks that re-approval refuses it.

## Every command

| Command | Does |
|---|---|
| `skillscout init` | Copy the example profile to `~/.skillscout/profile.toml` |
| `skillscout discover [--local [--full]] [--notify]` | Score the weekly index against your profile and write the report. `--local` crawls and scans GitHub on your machine instead; `--full` then ignores the caches; `--notify` sends a desktop notification (macOS, or Linux with `notify-send`) |
| `skillscout index [--out FILE]` | Crawl and scan the ecosystem and write the shared index (what the weekly job runs) |
| `skillscout list [--all] [-n N]` | Ranked candidates |
| `skillscout show <id>` | Flags, skills.sh audits, conflicts and the full SKILL.md |
| `skillscout approve <id> [--update] [--accept-risk] [--to DIR]` | Install and pin. `--to`, or `dir` under `[install]` in your profile, installs into another agent's skills folder |
| `skillscout installed` · `skillscout remove <id>` · `skillscout rollback <id>` | Pinned skills · uninstall · undo the last update |
| `skillscout updates` | Diff pinned skills against upstream |
| `skillscout init [--about "..."]` | Write the profile; `--about` drafts your keywords from one line |
| `skillscout history [--days N]` | A local digest of your recent Claude Code sessions, to draft personas from |
| `skillscout persona add <name> --about "..." [--keywords "a=3,b=2"]` | Add a persona; keywords drafted from the line unless given |
| `skillscout persona list` | Each persona: keywords, best installed skills, what's worth installing |
| `skillscout persona build <name> \| --all [--dry-run]` | Write `~/.claude/agents/<name>.md`, or update only its `skills:` list |
| `skillscout persona use [<name>...] [--core] [--clear]` | List only these personas' skills and plugins in Claude Code; `--clear` undoes it |
| `skillscout persona start <name>...` | `persona use`, then start Claude Code |
| `skillscout schedule` | Set up weekly discovery: launchd on macOS, a systemd timer on Linux (cron line without systemd), a `schtasks` command on Windows. It prints the one command that turns it on |

With the index, `discover` makes no GitHub API calls at all. For `--local` and `index`, set `GITHUB_TOKEN`, or just be logged in with the GitHub CLI (`gh auth login`), for higher API rate limits (5,000 requests an hour instead of 60). Following lists costs one lookup per linked repo: up to 1,500 a run with a token, 40 without; links not yet checked go first, so later runs reach the rest. A token is optional: without it, a rate limit means waiting rather than failing. A weekly run with few upstream changes takes about 25 seconds; the first run takes a few minutes. Scheduled jobs don't see your shell's environment, so add the token to the job if you want it there.

## Limits

- The scanner catches known patterns. It can't prove a skill is safe, so read `show` before you approve.
- Relevance is keyword matching against your profile, not semantic search. Tune the weights.
- The scanner is pattern rules, deliberately: a skill is text written to steer a model, so a model judging it can be steered by it. The rules make you read; they don't decide.
- Every installed skill's description loads into every agent session. `persona use` keeps only the active
  personas' skills listed; without it, keep the total to a few dozen.
- A persona's `skills:` load in full when its agent starts. `max_skills` (default 5) keeps that small.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

## License

MIT
