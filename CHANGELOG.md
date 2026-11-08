# Changelog

Upgrade with `pipx upgrade skillscout-cli`. Profile format changes are listed under each version; none so far
needed a migration.

## Unreleased

First run:
- `discover`, `list`, `show`, `approve` and `persona` without a profile now say to run `skillscout init --about "..."`
  instead of scoring the bundled example profile as if it were yours. Bare `skillscout` says the same.
- `init --about` writes your profile even when the index can't be downloaded, using your own words as keywords.
- The index download falls back to a mirror (raw.githubusercontent.com, then jsDelivr) when the GitHub release
  download fails, and gives up on a silent connection after 15 seconds instead of 60.
- A broken or missing profile gives one line naming the file and the problem, not a Python traceback.
- `--version`; help text for every flag and argument.

Installs and updates:
- `updates` names a symlink in an upstream skill but never reads it: a link to `~/.ssh/id_rsa` could otherwise
  put the key in the diff your agent reads.
- `approve --update` installs exactly the commit `updates` showed you, and refuses if upstream moved since.
  `--commit SHA` pins it by hand.
- `remove` refuses a copy you edited since install, unless you pass `--force`.
- `approve --to` refuses a skill that's already installed elsewhere, instead of leaving the first copy unpinned.
- `approve` on an unchanged skill says it's already current; on an existing copy it points to `--update`.
- `remove` and `rollback` accept a bare skill name, as `show` and `approve` do. An ambiguous name lists the matches.
- A symlinked `~/.claude/settings.json` (stow, chezmoi) stays a symlink when persona switching writes it.
- With `[index] verify = true`, a copy cached before verification was turned on is checked too.
- `approve` no longer reports an error after a successful install when the active persona was deleted.

Output:
- `list` and the report show one row per skill: copies at two paths or in forks show as "(+2 copies)".
  `list` has a header and a description column.
- Risk reads "none" everywhere when there are no flags.
- `persona build <name>` names an unknown persona and lists yours.
- `schedule` prints `launchctl bootstrap` / `bootout` (current macOS) instead of `load` / `unload`.

Profile format: unchanged. Text with emoji or newlines in `persona add` and `init --about` is now written as
valid TOML; a write that wouldn't parse is refused.

## 0.5.0 (7 Nov 2026)

- The weekly index carries skills.sh's Gen, Socket and Snyk audits for each skill (21,509 of 32,895 on the
  8 Nov index). Your machine never calls skills.sh.
- The report and `show` list them next to skillscout's own flags.
- `approve` refuses a skill that two or more auditors rate high or critical, unless you pass `--accept-risk`.

## 0.4.0 (1 Nov 2026)

- Persona switching: `persona use <name>...` keeps only those personas' skills listed in Claude Code (plus a
  `[switch] core` list) and turns off plugins pinned only to other personas. `--core`, `--clear`, and
  `persona start <name>` to switch and open Claude Code.
- Writes `skillOverrides` and `enabledPlugins` in `~/.claude/settings.json`; restores only what it changed.
- Profile format: personas take `skills`, `exclude` and `plugins` pins.
- Fix: skills installed as folder links were invisible on Python 3.13+.

## 0.3.0 (31 Oct 2026)

- Personas: one per kind of work, each with its own keywords, report section and Claude Code agent whose
  `skills:` list skillscout keeps current (`persona add / list / build`).
- `init --about` drafts keywords from one line; `history` gives a local digest of recent sessions.
- Profile format: `[personas.<name>]` tables with `description`, `agent`, `max_skills` and `keywords`.

## 0.2.0 (31 Oct 2026)

- Shared weekly index: `discover` downloads one signed file and scores it locally.
- Skill names can't escape the skills folder, updates swap atomically, and `rollback` restores the previous copy.
- `approve --to` installs into another agent's skills folder. Windows fixes.

## 0.1.0 (29 Oct 2026)

First release: weekly discovery, a scan of every file before install, installs pinned to a commit, upstream
diffs before any update, weekly scheduling on macOS, Linux and Windows.
