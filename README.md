# docker-prune-plan

Dry-run view of what `docker system prune` would delete. Implemented as a Python CLI.

## Install

- With `pipx` (recommended): run `pipx install .` from this repo.
- With `pip`: run `pip install .`

After installation the command `docker-prune-plan` will be available on your PATH.

## Usage

```bash
# Default: system view (containers, volumes, images)
docker-prune-plan

# See unused images too (even named ones)
docker-prune-plan --all

# Limit to a specific object type
docker-prune-plan --type volume

# JSON output
docker-prune-plan --json
```

Requires Docker to be installed and the daemon running.
