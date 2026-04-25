# Pedia — Flotilla plugin contributions

When a downstream project runs `flotilla install pedia`, the Flotilla
CLI pip-installs Pedia and copies/symlinks the contributions in this
directory into the consumer's `.claude/`.

See `flotilla.yaml` one level up for the manifest. See
[github.com/ocgully/flotilla](https://github.com/ocgully/flotilla) for
the CLI.

## What ships

- `agents/pedia-keeper.md` — the agent that queries the knowledge base
- `commands/pedia.md` — `/pedia` Claude Code command for spec/decision context
