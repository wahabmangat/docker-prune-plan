# docker-prune-plan
**See exactly what `docker system prune` is about to wipe before you hit the big red button and regret it.**

Dry-run view of what `docker system prune` would delete (containers, images, build cache, unused networks; volumes when requested with `--volumes`). Implemented as a Python CLI.

## Install
Install the latest release directly:
```bash
pip install https://github.com/wahabmangat/docker-prune-plan/releases/download/v0.1.1/docker_prune_plan-0.1.1-py3-none-any.whl
```

After installation the command `docker-prune-plan` will be available on your PATH.

## Usage

```bash
# Default: system view (containers, images, build cache, networks)
docker-prune-plan

# See unused images too (even named ones)
docker-prune-plan --all

# Include volumes (matches docker system prune --volumes)
docker-prune-plan --volumes

# Limit to a specific object type
docker-prune-plan --type volume   # or image|container|network|build-cache

# JSON output
docker-prune-plan --json
```

Requires Docker to be installed and the daemon running. Totals use `docker system df`, so shared layers are not double-counted.
