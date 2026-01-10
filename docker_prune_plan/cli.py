from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Sequence, Set, Tuple

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


def render_table(
    items: Sequence[PruneItem], exclude_columns: Set[str] | None = None
) -> str:
    if exclude_columns is None:
        exclude_columns = set()
    all_headers = ["TYPE", "ID", "NAME", "SIZE", "INFO"]
    headers = [h for h in all_headers if h not in exclude_columns]
    column_indices = [i for i, h in enumerate(all_headers) if h not in exclude_columns]

    rows: List[List[str]] = [
        [item.item_type, item.item_id, item.name, item.human_size, item.description]
        for item in items
    ]
    rows = [[row[i] for i in column_indices] for row in rows]

    widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(str(cell)))
    lines = ["  ".join(headers[idx].ljust(widths[idx]) for idx in range(len(headers)))]
    for row in rows:
        lines.append(
            "  ".join(str(row[idx]).ljust(widths[idx]) for idx in range(len(headers)))
        )
    return "\n".join(lines)


def short_id(full_id: str) -> str:
    if not full_id:
        return ""
    if full_id.startswith("sha256:"):
        full_id = full_id.split(":", 1)[1]
    return full_id[:12]


def collect_used_volumes(containers: Sequence[Dict[str, object]]) -> Set[str]:
    used: Set[str] = set()
    for container in containers:
        for mount in container.get("Mounts", []) or []:
            if mount.get("Type") == "volume" and mount.get("Name"):
                used.add(str(mount["Name"]))
    return used


def normalize_image_id(v: str) -> str:
    if not v:
        return ""
    return v if v.startswith("sha256:") else f"sha256:{v}"


def collect_used_images(containers: Sequence[Dict[str, object]]) -> Set[str]:
    used: Set[str] = set()
    for container in containers:
        image_id = container.get("ImageID")
        if image_id:
            used.add(normalize_image_id(str(image_id)))
            continue

        v = container.get("Image")
        if v:
            sv = str(v)
            if sv.startswith("sha256:") or re.fullmatch(r"[0-9a-f]{64}", sv):
                used.add(normalize_image_id(sv))
    return used


def is_probably_anonymous_volume(name: str) -> bool:
    if not name:
        return False
    return re.fullmatch(r"[0-9a-f]{32,64}", name) is not None


def build_plan_container(client: docker.APIClient) -> Tuple[List[PruneItem], int]:
    plan: List[PruneItem] = []
    total = 0
    stopped = client.containers(
        all=True, filters={"status": ["created", "exited", "dead"]}, size=True
    )
    for container in stopped:
        names = container.get("Names") or []
        name = names[0].lstrip("/") if names else ""
        status = container.get("Status") or ""
        size = int(container.get("SizeRw") or 0)
        total += size
        plan.append(
            PruneItem(
                item_type="Container",
                item_id=short_id(container.get("Id") or ""),
                name=name,
                size=size,
                human_size=human_size(size),
                description=f"Status: {status}" if status else "Stopped container",
            )
        )
    return plan, total


def build_plan_image(
    client: docker.APIClient, include_all: bool
) -> Tuple[List[PruneItem], int]:
    plan: List[PruneItem] = []
    total = 0
    all_containers = client.containers(all=True)
    used_images = collect_used_images(all_containers)

    img_filters = {"dangling": True} if not include_all else {}
    images = client.images(all=True, filters=img_filters)

    for image in images:
        image_id = image.get("Id") or ""
        if include_all and image_id in used_images:
            continue

        size = int(image.get("Size") or 0)
        total += size

        tags = image.get("RepoTags") or []
        name = ", ".join(tags) if tags else "<none>"

        created = image.get("Created")
        created_str = ""
        if isinstance(created, (int, float)) and created > 0:
            created_str = datetime.fromtimestamp(created, tz=timezone.utc).isoformat()

        reason = "Dangling image" if not include_all else "Unused image (no containers)"
        desc = reason
        if created_str:
            desc = f"{reason}; Created: {created_str}"

        plan.append(
            PruneItem(
                item_type="Image",
                item_id=short_id(image_id),
                name=name,
                size=size,
                human_size=human_size(size),
                description=desc,
            )
        )

    return plan, total


def build_plan_volume(
    client: docker.APIClient, include_all: bool, system_mode: bool
) -> Tuple[List[PruneItem], int]:
    plan: List[PruneItem] = []
    total = 0

    all_containers = client.containers(all=True)
    used_volumes = collect_used_volumes(all_containers)

    df_data = client.df()
    volumes = df_data.get("Volumes") or []

    for volume in volumes:
        name = volume.get("Name") or ""
        if not name or name in used_volumes:
            continue

        if not include_all and not is_probably_anonymous_volume(name):
            continue

        usage = volume.get("UsageData") or {}
        size = int(usage.get("Size") or 0)
        total += size

        if system_mode:
            reason = "Unused volume (anonymous)"
        else:
            reason = "Unused volume (anonymous)" if not include_all else "Unused volume"

        plan.append(
            PruneItem(
                item_type="Volume",
                item_id=name,
                name=name,
                size=size,
                human_size=human_size(size),
                description=reason,
            )
        )

    return plan, total


def build_plan_network(client: docker.APIClient) -> Tuple[List[PruneItem], int]:
    plan: List[PruneItem] = []
    total = 0

    networks = client.networks()
    for net in networks:
        net_id = net.get("Id") or ""
        name = net.get("Name") or ""
        if name in {"bridge", "host", "none"}:
            continue

        try:
            info = client.inspect_network(net_id)
        except DockerException:
            continue

        containers = info.get("Containers") or {}
        if containers:
            continue

        plan.append(
            PruneItem(
                item_type="Network",
                item_id=short_id(net_id),
                name=name,
                size=0,
                human_size="0B",
                description="Unused network",
            )
        )

    return plan, total


def build_plan_build_cache(client: docker.APIClient) -> Tuple[List[PruneItem], int]:
    plan: List[PruneItem] = []
    total = 0
    df_data = client.df()
    build_cache = df_data.get("BuildCache") or []
    for entry in build_cache:
        if entry.get("InUse"):
            continue
        size = int(entry.get("Size") or 0)
        total += size
        build_id = entry.get("ID") or ""
        desc = entry.get("Description") or ""
        last_used = entry.get("LastUsedAt") or ""
        info = "; ".join([p for p in [desc, f"Last used: {last_used}"] if p])
        plan.append(
            PruneItem(
                item_type="BuildCache",
                item_id=short_id(build_id),
                name=desc,
                size=size,
                human_size=human_size(size),
                description=info,
            )
        )
    return plan, total


def build_plan_system(
    client: docker.APIClient, include_all_images: bool, include_volumes: bool
) -> Tuple[List[PruneItem], int]:
    plan: List[PruneItem] = []
    total = 0

    items, t = build_plan_container(client)
    plan.extend(items)
    total += t

    items, t = build_plan_network(client)
    plan.extend(items)
    total += t

    items, t = build_plan_image(client, include_all_images)
    plan.extend(items)
    total += t

    if include_volumes:
        items, t = build_plan_volume(client, include_all=False, system_mode=True)
        plan.extend(items)
        total += t

    items, t = build_plan_build_cache(client)
    plan.extend(items)
    total += t

    return plan, total


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="docker-prune-plan",
        description=(
            "Preview what docker prune commands would remove, grouped by resource type "
            "and reclaimable space."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_image = sub.add_parser(
        "image",
        help="List images that would be pruned",
        description="Show dangling or unused images and their reclaimable space.",
    )
    p_image.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="Include unused images referenced by no containers (not just dangling images).",
    )
    p_image.add_argument(
        "--json",
        action="store_true",
        help="Output the plan as JSON instead of a table.",
    )

    p_volume = sub.add_parser(
        "volume",
        help="List volumes that would be pruned",
        description="Show unused volumes and their reclaimable space.",
    )
    p_volume.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="Include named volumes; by default only anonymous volumes are included.",
    )
    p_volume.add_argument(
        "--json",
        action="store_true",
        help="Output the plan as JSON instead of a table.",
    )

    p_container = sub.add_parser(
        "container",
        help="List stopped containers that would be pruned",
        description="Show stopped containers eligible for pruning and their reclaimable space.",
    )
    p_container.add_argument(
        "--json",
        action="store_true",
        help="Output the plan as JSON instead of a table.",
    )

    p_network = sub.add_parser(
        "network",
        help="List unused networks that would be pruned",
        description="Show user-defined networks with no attached containers.",
    )
    p_network.add_argument(
        "--json",
        action="store_true",
        help="Output the plan as JSON instead of a table.",
    )

    p_system = sub.add_parser(
        "system",
        help="List everything that would be pruned",
        description=(
            "Show containers, networks, images, optional volumes, and build cache "
            "that would be removed by docker system prune."
        ),
    )
    p_system.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="Include unused images referenced by no containers (not just dangling images).",
    )
    p_system.add_argument(
        "--volumes",
        action="store_true",
        help="Include unused volumes in the plan (matches docker system prune --volumes).",
    )
    p_system.add_argument(
        "--name",
        action="store_true",
        help="Show the NAME column in the system plan output table.",
    )
    p_system.add_argument(
        "--json",
        action="store_true",
        help="Output the plan as JSON instead of the table default.",
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
        if args.cmd == "container":
            plan, plan_total = build_plan_container(client)

        elif args.cmd == "network":
            plan, plan_total = build_plan_network(client)

        elif args.cmd == "image":
            plan, plan_total = build_plan_image(client, include_all=bool(args.all))

        elif args.cmd == "volume":
            plan, plan_total = build_plan_volume(
                client, include_all=bool(args.all), system_mode=False
            )

        elif args.cmd == "system":
            plan, plan_total = build_plan_system(
                client,
                include_all_images=bool(args.all),
                include_volumes=bool(args.volumes),
            )

        else:
            raise SystemExit(2)

    except DockerException as exc:
        print(f"Docker error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()

    if args.json:
        out = {
            "command": args.cmd,
            "items": [i.to_dict() for i in plan],
            "plan_reclaimable_bytes": plan_total,
        }
        print(json.dumps(out, indent=2))
        return

    exclude_columns: Set[str] = set()
    if args.cmd == "system" and not args.name:
        exclude_columns.add("NAME")

    print(render_table(plan, exclude_columns=exclude_columns))
    print(f"\nPlan Reclaimable Space: {human_size(plan_total)}")


if __name__ == "__main__":
    main()
