# my-agents

My repository to keep agents, skills, etc.

## claudesync

A small CLI to sync Claude Code agents and skills between this repo and `~/.claude`.

### Commands

| Command | What it does |
| --- | --- |
| `claudesync backup` | Copy `agents/` and `skills/` from `~/.claude` into this repo |
| `claudesync install` | Copy `agents/` and `skills/` from this repo into `~/.claude` |
| `claudesync link` | Symlink the script into `~/.local/bin` so it runs from anywhere |
| `claudesync unlink` | Remove the symlink |
| `claudesync where` | Print the resolved repo path |
| `claudesync help` | Show help |

Sync uses `rsync -a --delete`, so the destination becomes a mirror of the source (files removed from source are removed from destination).

### Install the CLI

**1. Make sure `~/.local/bin` is on your PATH** (one-time, skip if already set up)

Add to `~/.zshrc`:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

Reload your shell:

```sh
source ~/.zshrc
```

**2. Link the script from this repo**

```sh
cd /path/to/my-agents
./claudesync link
```

This creates a symlink `~/.local/bin/claudesync` pointing to the script in this repo. The repo stays where it is — the symlink is just a shortcut on your PATH.

**3. Verify**

```sh
claudesync where    # prints the repo path
claudesync help
```

### Usage

After install, run from anywhere:

```sh
claudesync backup     # pull from ~/.claude into the repo
claudesync install    # push from the repo into ~/.claude
```

### Updating

Edits to the script in the repo take effect immediately — the symlink follows the file. If you move the repo, re-run `claudesync link` from its new location.
