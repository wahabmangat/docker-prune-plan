from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Sequence, Set, Tuple
import subprocess
import re

import docker
from docker.errors import DockerException
from docker.utils import kwargs_from_env


@dataclass
class PruneItem:
    item_type: str
    item_id: str
    name: str = ""
    size: int = 0
    human_size: str = ""
    description: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "type": self.item_type,
            "id": self.item_id,
            "name": self.name,
            "size": self.size,
            "human_size": self.human_size,
            "description": self.description,
        }


def human_size(num: int) -> str:
    if num == 0:
        return "0B"

    suffixes = ["B", "kB", "MB", "GB", "TB", "PB", "EB"]
    negative = num < 0
    value = abs(float(num))
    suffix = suffixes[0]

    for suffix in suffixes:
        if value < 1000 or suffix == suffixes[-1]:
            break
        value /= 1000.0

    formatted = f"{value:.1f}{suffix}"
    if formatted.endswith(".0" + suffix):
        formatted = formatted.replace(".0" + suffix, suffix)
    return f"-{formatted}" if negative else formatted


def parse_human_size_to_bytes(text: str) -> int | None:
    """Parse strings like '4.2GB' or '512MB' into bytes."""
    match = re.match(r"^\s*([0-9]*\.?[0-9]+)\s*([KMGTP]?B)\s*$", text, re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).upper()
    multipliers = {
        "B": 1,
        "KB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "TB": 1000**4,
        "PB": 1000**5,
    }
    return int(value * multipliers.get(unit, 1))


def collect_used_volumes(containers: Sequence[Dict[str, object]]) -> Set[str]:
    used: Set[str] = set()
    for container in containers:
        for mount in container.get("Mounts", []) or []:
            if mount.get("Type") == "volume" and mount.get("Name"):
                used.add(str(mount["Name"]))
    return used


def collect_used_images(containers: Sequence[Dict[str, object]]) -> Set[str]:
    used: Set[str] = set()
    for container in containers:
        image_id = container.get("ImageID")
        if image_id:
            used.add(str(image_id))
    return used


def build_plan(
    prune_type: str,
    include_all_images: bool,
    include_volumes: bool,
    client: docker.APIClient,
) -> Tuple[List[PruneItem], int]:
    plan: List[PruneItem] = []
    total_size = 0

    df_data: Dict[str, object] | None = None
    all_containers = client.containers(all=True)

    if prune_type in {"system", "container"}:
        stopped = client.containers(
            all=True, filters={"status": ["exited", "dead"]}, size=True
        )
        for container in stopped:
            names = container.get("Names") or []
            name = names[0].lstrip("/") if names else ""
            status = container.get("Status") or ""
            size = int(container.get("SizeRw") or 0)
            human = human_size(size) if size else "0B"
            total_size += size
            plan.append(
                PruneItem(
                    item_type="Container",
                    item_id=(container.get("Id") or "")[:12],
                    name=name,
                    size=size,
                    human_size=human,
                    description=f"Status: {status}",
                )
            )

    if prune_type in {"system", "volume"}:
        if prune_type == "system" and not include_volumes:
            pass
        else:
            used_volumes = collect_used_volumes(all_containers)
            if df_data is None:
                df_data = client.df()
            volumes = df_data.get("Volumes") or []
            for volume in volumes:
                name = volume.get("Name") or ""
                if not name or name in used_volumes:
                    continue

                usage = volume.get("UsageData") or {}
                size = int(usage.get("Size") or 0)
                human = human_size(size) if usage else "0B"
                if usage:
                    total_size += size

                plan.append(
                    PruneItem(
                        item_type="Volume",
                        item_id=name,
                        name=name,
                        size=size,
                        human_size=human,
                        description="Unused volume",
                    )
                )

    if prune_type in {"system", "image"}:
        used_images = collect_used_images(all_containers)
        img_filters = {"dangling": True} if not include_all_images else {}
        images = client.images(all=True, filters=img_filters)
        for image in images:
            image_id = image.get("Id") or ""
            if include_all_images and image_id in used_images:
                continue

            size = int(image.get("Size") or 0)
            total_size += size

            tags = image.get("RepoTags") or []
            name = ", ".join(tags) if tags else "<none>"

            created = image.get("Created")
            created_str = ""
            if isinstance(created, (int, float)) and created > 0:
                created_str = datetime.fromtimestamp(
                    created, tz=timezone.utc
                ).isoformat()

            plan.append(
                PruneItem(
                    item_type="Image",
                    item_id=image_id[7:19] if len(image_id) >= 19 else image_id,
                    name=name,
                    size=size,
                    human_size=human_size(size),
                    description=f"Created: {created_str}" if created_str else "",
                )
            )

    if prune_type in {"system", "network"}:
        networks = client.networks(filters={"dangling": True})
        for net in networks:
            name = net.get("Name") or ""
            if name in {"bridge", "host", "none", "ingress"}:
                continue

            plan.append(
                PruneItem(
                    item_type="Network",
                    item_id=(net.get("Id") or "")[:12],
                    name=name,
                    human_size="0B",
                    description="Unused network",
                )
            )

    if prune_type in {"system", "build-cache"}:
        if df_data is None:
            df_data = client.df()
        build_cache = df_data.get("BuildCache") or []
        for entry in build_cache:
            if entry.get("InUse"):
                continue
            size = int(entry.get("Size") or 0)
            total_size += size
            build_id = entry.get("ID") or ""
            desc = entry.get("Description") or ""
            last_used = entry.get("LastUsedAt") or ""
            desc_parts = [part for part in [desc, f"Last used: {last_used}"] if part]

            plan.append(
                PruneItem(
                    item_type="BuildCache",
                    item_id=build_id[:12],
                    name=desc,
                    size=size,
                    human_size=human_size(size),
                    description="; ".join(desc_parts),
                )
            )

    return plan, total_size


def docker_df_reclaimable_bytes(
    prune_type: str, include_volumes: bool
) -> int | None:
    """
    Use `docker system df --format '{{json .}}'` to get reclaimable bytes as reported by Docker,
    filtered to the selected prune type. Returns None if the command is unavailable or fails.
    """
    allowed_types: Set[str]
    if prune_type == "system":
        allowed_types = {"Images", "Containers", "Build Cache"}
        if include_volumes:
            allowed_types.add("Local Volumes")
    elif prune_type == "image":
        allowed_types = {"Images"}
    elif prune_type == "container":
        allowed_types = {"Containers"}
    elif prune_type == "volume":
        allowed_types = {"Local Volumes"}
    elif prune_type == "build-cache":
        allowed_types = {"Build Cache"}
    elif prune_type == "network":
        allowed_types = set()
    else:
        allowed_types = {"Images", "Containers", "Build Cache"}
    try:
        result = subprocess.run(
            ["docker", "system", "df", "--format", "{{json .}}"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    total = 0
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("Type") not in allowed_types:
            continue
        reclaimable_field = entry.get("Reclaimable")
        if not reclaimable_field:
            continue
        size_text = reclaimable_field.split("(", 1)[0].strip()
        bytes_val = parse_human_size_to_bytes(size_text)
        if bytes_val is not None:
            total += bytes_val
    return total


def render_table(items: Sequence[PruneItem]) -> str:
    headers = ["TYPE", "ID", "NAME", "SIZE", "INFO"]
    rows: List[List[str]] = [
        [item.item_type, item.item_id, item.name, item.human_size, item.description]
        for item in items
    ]

    widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(str(cell)))

    lines = [
        "  ".join(headers[idx].ljust(widths[idx]) for idx in range(len(headers)))
    ]
    for row in rows:
        lines.append(
            "  ".join(str(row[idx]).ljust(widths[idx]) for idx in range(len(headers)))
        )
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dry-run view of what docker system prune would delete."
    )
    parser.add_argument(
        "--type",
        choices=["system", "image", "container", "volume", "network", "build-cache"],
        default="system",
        help="Type to prune: system, image, container, volume, network, build-cache",
    )
    parser.add_argument(
        "--all",
        dest="include_all",
        action="store_true",
        help="Remove all unused images, not just dangling ones",
    )
    parser.add_argument(
        "--volumes",
        dest="include_volumes",
        action="store_true",
        help="Include unused volumes (matches docker system prune --volumes)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)

    try:
        client = docker.APIClient(version="auto", **kwargs_from_env())
    except DockerException as exc:
        print(f"Error connecting to Docker: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        plan, total_size = build_plan(
            args.type, args.include_all, args.include_volumes, client
        )
    except DockerException as exc:
        print(f"Docker error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()

    if args.json:
        json.dump([item.to_dict() for item in plan], sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    print(render_table(plan))
    docker_reclaimable = docker_df_reclaimable_bytes(
        args.type, args.include_volumes
    )
    total_bytes = docker_reclaimable if docker_reclaimable is not None else total_size
    label = (
        "Reclaimable Space (docker system df)"
        if docker_reclaimable is not None
        else "Total Reclaimable Space (approx)"
    )
    print(f"\n{label}: {human_size(total_bytes)}")


if __name__ == "__main__":
    main()
