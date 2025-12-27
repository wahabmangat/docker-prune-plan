from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Sequence, Set, Tuple

import docker
from docker.errors import DockerException
from docker.utils import kwargs_from_env


@dataclass
class PruneItem:
    type: str
    id: str
    name: str = ""
    size: int = 0
    human_size: str = ""
    description: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "type": self.type,
            "id": self.id,
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
    prune_type: str, include_all_images: bool, client: docker.APIClient
) -> Tuple[List[PruneItem], int]:
    plan: List[PruneItem] = []
    total_size = 0

    all_containers = client.containers(all=True)

    if prune_type in {"system", "container"}:
        stopped = client.containers(
            all=True, filters={"status": ["exited", "dead"]}
        )
        for container in stopped:
            names = container.get("Names") or []
            name = names[0].lstrip("/") if names else ""
            status = container.get("Status") or ""
            plan.append(
                PruneItem(
                    type="Container",
                    id=(container.get("Id") or "")[:12],
                    name=name,
                    description=f"Status: {status}",
                )
            )

    if prune_type in {"system", "volume"}:
        used_volumes = collect_used_volumes(all_containers)
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
                    type="Volume",
                    id=name,
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
                    type="Image",
                    id=image_id[7:19] if len(image_id) >= 19 else image_id,
                    name=name,
                    size=size,
                    human_size=human_size(size),
                    description=f"Created: {created_str}" if created_str else "",
                )
            )

    return plan, total_size


def render_table(items: Sequence[PruneItem]) -> str:
    headers = ["TYPE", "ID", "NAME", "SIZE", "INFO"]
    rows: List[List[str]] = [
        [item.type, item.id, item.name, item.human_size, item.description]
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
        choices=["system", "image", "container", "volume"],
        default="system",
        help="Type to prune: system, image, container, volume",
    )
    parser.add_argument(
        "--all",
        dest="include_all",
        action="store_true",
        help="Remove all unused images, not just dangling ones",
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
        plan, total_size = build_plan(args.type, args.include_all, client)
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
    print(f"\nTotal Reclaimable Space (approx): {human_size(total_size)}")


if __name__ == "__main__":
    main()
