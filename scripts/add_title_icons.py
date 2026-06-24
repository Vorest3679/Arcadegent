#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Tuple

BASE_URL = "https://map.bemanicn.com/imgs/titles"
DEFAULT_ICONS_DIR = Path("data/assets/bemanicn/titles")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download title icon PNG assets and merge local icon fields into JSON/JSONL data."
    )
    parser.add_argument("--input", type=Path, required=True, help="Input .json or .jsonl file path.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path. If omitted, writes to input path with .icons suffix.",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite the input file directly.",
    )
    parser.add_argument(
        "--icons-dir",
        type=Path,
        default=DEFAULT_ICONS_DIR,
        help="Directory to store downloaded title icons.",
    )
    parser.add_argument(
        "--index-output",
        type=Path,
        default=None,
        help="Icon index json output path. Default: <icons-dir>/title_icons_index.json",
    )
    parser.add_argument("--timeout", type=float, default=12.0, help="HTTP timeout in seconds.")
    parser.add_argument("--retries", type=int, default=2, help="Retries for each icon download.")
    parser.add_argument("--retry-backoff", type=float, default=0.8, help="Backoff seconds * attempt.")
    parser.add_argument(
        "--title-props",
        type=Path,
        default=None,
        help="Optional JSON/JSONL source containing titles.name + titles.img mapping.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not download files. Only collect titles and rewrite records.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="Skip download when icon file already exists (default on).",
    )
    return parser.parse_args()


def normalize_title_name(title_name: Any) -> str | None:
    if title_name is None:
        return None
    title_name_str = str(title_name).strip()
    if not title_name_str:
        return None
    return title_name_str


def iter_title_name_refs(node: Any) -> Iterator[Tuple[Dict[str, Any], str]]:
    if isinstance(node, dict):
        if "title_name" in node:
            yield node, "title_name"
        for value in node.values():
            yield from iter_title_name_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_title_name_refs(item)


def safe_icon_filename(title_name: str) -> str:
    raw = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", title_name).strip(" .")
    if not raw:
        raw = "title"
    digest = hashlib.sha1(title_name.encode("utf-8")).hexdigest()[:10]
    return f"{raw}_{digest}.png"


def icon_url_for(title_name: str) -> str:
    encoded = urllib.parse.quote(title_name, safe="")
    return f"{BASE_URL}/{encoded}.png"


def download_icon(
    *,
    title_name: str,
    remote_urls: list[str],
    icons_dir: Path,
    timeout: float,
    retries: int,
    retry_backoff: float,
    dry_run: bool,
    skip_existing: bool,
) -> Dict[str, Any]:
    candidate_urls = [u for u in remote_urls if isinstance(u, str) and u.strip()]
    if not candidate_urls:
        candidate_urls = [icon_url_for(title_name)]
    last_error: str | None = None
    for remote_url in candidate_urls:
        suffix = Path(urllib.parse.urlparse(remote_url).path).suffix.lower() or ".png"
        filename = safe_icon_filename(title_name).removesuffix(".png") + suffix
        local_path = icons_dir / filename

        if local_path.exists() and skip_existing:
            return {
                "title_name": title_name,
                "remote_url": remote_url,
                "icon_file": filename,
                "status": "exists",
                "bytes": local_path.stat().st_size,
            }

        if dry_run:
            return {
                "title_name": title_name,
                "remote_url": remote_url,
                "icon_file": filename,
                "status": "dry_run",
                "bytes": 0,
            }

        attempts = max(0, retries) + 1
        for attempt in range(1, attempts + 1):
            try:
                req = urllib.request.Request(remote_url, headers={"User-Agent": "Arcadegent/1.0"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = resp.read()
                    content_type = str(resp.headers.get("Content-Type") or "")
                    if resp.status != 200:
                        raise RuntimeError(f"http_status={resp.status}")
                    if body.startswith(b"<!DOCTYPE html") or body.startswith(b"<html"):
                        raise RuntimeError("received html instead of image")
                    if content_type and "image" not in content_type.lower():
                        raise RuntimeError(f"unexpected content-type: {content_type}")
                icons_dir.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(body)
                return {
                    "title_name": title_name,
                    "remote_url": remote_url,
                    "icon_file": filename,
                    "status": "downloaded",
                    "bytes": len(body),
                }
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, RuntimeError) as exc:
                last_error = str(exc)
                if attempt < attempts:
                    time.sleep(max(0.0, retry_backoff) * attempt)
                continue

    return {
        "title_name": title_name,
        "remote_url": candidate_urls[0],
        "icon_file": safe_icon_filename(title_name),
        "status": "failed",
        "bytes": 0,
        "error": last_error,
    }


def collect_titles_from_jsonl(path_in: Path) -> set[str]:
    titles: set[str] = set()
    with path_in.open("r", encoding="utf-8") as fin:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            for ref, key in iter_title_name_refs(obj):
                t = normalize_title_name(ref.get(key))
                if t:
                    titles.add(t)
    return titles


def collect_titles_from_json(path_in: Path) -> set[str]:
    payload = json.loads(path_in.read_text(encoding="utf-8"))
    titles: set[str] = set()
    for ref, key in iter_title_name_refs(payload):
        t = normalize_title_name(ref.get(key))
        if t:
            titles.add(t)
    return titles


def maybe_read_json_or_jsonl(path: Path) -> Iterator[Any]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        yield json.loads(path.read_text(encoding="utf-8"))
        return
    if suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as fin:
            for line in fin:
                line = line.strip()
                if line:
                    yield json.loads(line)
        return
    raise ValueError(f"Unsupported mapping file extension: {path}")


def build_title_img_map(path: Path | None) -> Dict[str, str]:
    if path is None or not path.exists():
        return {}
    mapping: Dict[str, str] = {}
    for obj in maybe_read_json_or_jsonl(path):
        if not isinstance(obj, dict):
            continue
        titles = obj.get("titles")
        if not isinstance(titles, dict):
            continue
        names = titles.get("name")
        imgs = titles.get("img")
        if not isinstance(names, dict) or not isinstance(imgs, dict):
            continue
        for title_id, title_name in names.items():
            t = normalize_title_name(title_name)
            rel = imgs.get(str(title_id))
            if not t or not isinstance(rel, str) or not rel.strip():
                continue
            rel_clean = rel.strip().lstrip("/")
            mapping[t] = f"https://map.bemanicn.com/{rel_clean}"
    return mapping


def rewrite_node_with_icon(node: Any, icon_map: Dict[str, Dict[str, Any]]) -> Any:
    if isinstance(node, dict):
        if "title_name" in node:
            t = normalize_title_name(node.get("title_name"))
            icon_info = icon_map.get(t) if t else None
            node.pop("title_icon_url", None)
            node["title_icon_file"] = icon_info.get("icon_file") if icon_info else None
            node["title_icon_status"] = icon_info.get("status") if icon_info else "missing_title_name"
            if icon_info:
                node["title_icon_remote"] = icon_info.get("remote_url")
        for k, v in list(node.items()):
            node[k] = rewrite_node_with_icon(v, icon_map)
        return node
    if isinstance(node, list):
        return [rewrite_node_with_icon(item, icon_map) for item in node]
    return node


def output_path_for(input_path: Path, explicit_output: Path | None, in_place: bool) -> Path:
    if in_place:
        return input_path
    if explicit_output is not None:
        return explicit_output
    if input_path.suffix.lower() == ".jsonl":
        return input_path.with_name(f"{input_path.stem}.icons.jsonl")
    return input_path.with_name(f"{input_path.stem}.icons.json")


def process_json(path_in: Path, path_out: Path, icon_map: Dict[str, Dict[str, Any]]) -> int:
    payload = json.loads(path_in.read_text(encoding="utf-8"))
    enriched = rewrite_node_with_icon(payload, icon_map)
    path_out.parent.mkdir(parents=True, exist_ok=True)
    path_out.write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1


def process_jsonl(path_in: Path, path_out: Path, icon_map: Dict[str, Dict[str, Any]]) -> int:
    count = 0
    path_out.parent.mkdir(parents=True, exist_ok=True)
    with path_in.open("r", encoding="utf-8") as fin, path_out.open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            enriched = rewrite_node_with_icon(obj, icon_map)
            fout.write(json.dumps(enriched, ensure_ascii=False))
            fout.write("\n")
            count += 1
    return count


def download_all_icons(args: argparse.Namespace, title_names: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    icon_map: Dict[str, Dict[str, Any]] = {}
    title_img_map = build_title_img_map(args.title_props)
    titles_sorted = sorted(set(title_names))
    total = len(titles_sorted)
    for i, title_name in enumerate(titles_sorted, 1):
        remote_urls: list[str] = []
        mapped = title_img_map.get(title_name)
        if mapped:
            remote_urls.append(mapped)
            p = urllib.parse.urlparse(mapped)
            base = p.path.rsplit(".", 1)[0] if "." in p.path else p.path
            for ext in [".png", ".jpg", ".jpeg", ".webp"]:
                alt = urllib.parse.urlunparse((p.scheme, p.netloc, base + ext, "", "", ""))
                remote_urls.append(alt)
        remote_urls.append(icon_url_for(title_name))
        remote_urls = list(dict.fromkeys(remote_urls))
        info = download_icon(
            title_name=title_name,
            remote_urls=remote_urls,
            icons_dir=args.icons_dir,
            timeout=args.timeout,
            retries=args.retries,
            retry_backoff=args.retry_backoff,
            dry_run=args.dry_run,
            skip_existing=args.skip_existing,
        )
        icon_map[title_name] = info
        if i % 20 == 0 or i == total:
            print(f"[icons] progress {i}/{total}", flush=True)
    return icon_map


def write_icon_index(path: Path, icon_map: Dict[str, Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [icon_map[k] for k in sorted(icon_map.keys())]
    summary = {
        "total": len(rows),
        "downloaded": sum(1 for r in rows if r.get("status") == "downloaded"),
        "exists": sum(1 for r in rows if r.get("status") == "exists"),
        "dry_run": sum(1 for r in rows if r.get("status") == "dry_run"),
        "failed": sum(1 for r in rows if r.get("status") == "failed"),
    }
    payload = {"summary": summary, "icons": rows}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    path_in = args.input
    if not path_in.exists():
        raise FileNotFoundError(f"Input file not found: {path_in}")

    path_out = output_path_for(path_in, args.output, args.in_place)
    index_path = args.index_output or (args.icons_dir / "title_icons_index.json")
    suffix = path_in.suffix.lower()

    if suffix == ".json":
        titles = collect_titles_from_json(path_in)
    elif suffix == ".jsonl":
        titles = collect_titles_from_jsonl(path_in)
    else:
        raise ValueError("Only .json or .jsonl files are supported.")

    print(f"[titles] unique={len(titles)} props_map={args.title_props}", flush=True)
    icon_map = download_all_icons(args, titles)
    write_icon_index(index_path, icon_map)

    if suffix == ".json":
        rows = process_json(path_in, path_out, icon_map)
    else:
        rows = process_jsonl(path_in, path_out, icon_map)

    print(
        f"[ok] processed={rows} input={path_in} output={path_out} "
        f"icons_dir={args.icons_dir} index={index_path}",
        flush=True,
    )


if __name__ == "__main__":
    main()
