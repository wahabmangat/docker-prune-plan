# docker-prune-plan

[![PyPI version](https://badge.fury.io/py/docker-prune-plan.svg)](https://badge.fury.io/py/docker-prune-plan)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**See what Docker’s prune commands are about to wipe before you hit the big red button and regret it.**

Best-effort preview of what Docker prune commands would delete (`system`, `image`, `container`, `volume`, `network`). Implemented as a Python CLI.

## Install

### Option 1: PyPI (Recommended)
The easiest way to install the latest stable version:

```bash
pip install docker-prune-plan
```
### Option 2: Github releases
Install the latest release directly:
```bash
pip install https://github.com/wahabmangat/docker-prune-plan/releases/download/v0.2.1/docker_prune_plan-0.2.1-py3-none-any.whl
```

After installation the command `docker-prune-plan` will be available on your PATH.

## Usage

```bash
# Preview docker system prune
docker-prune-plan system

# Preview docker system prune -a (unused images, not just dangling)
docker-prune-plan system --all/-a

# Preview docker system prune --volumes (unused anonymous volumes)
docker-prune-plan system --volumes

# Preview docker image prune (dangling images)
docker-prune-plan image

# Preview docker image prune -a (all unused images)
docker-prune-plan image --all/-a

# Preview docker volume prune (unused anonymous volumes)
docker-prune-plan volume

# Preview docker volume prune -a (all unused volumes)
docker-prune-plan volume --all/-a

# Preview docker container prune (stopped containers)
docker-prune-plan container

# Preview docker network prune (unused custom networks)
docker-prune-plan network

# JSON output
docker-prune-plan system --json

```
## Notes
- --all is supported for system and image (affects images only), and for volume (includes named volumes).
- Label/Filter support is not implemented yet.
- The tool prints a **Plan Reclaimable Space** total based on the listed items. Differences from other Docker disk usage reports can occur due to shared image layers and build cache internals.
- The output is a best-effort preview based on the current Docker state (useful as a safety checklist before pruning). Actual prune results may differ due to Docker’s prune order and state changes.