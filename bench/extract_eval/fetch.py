"""Download the extraction-eval data (WCXB docs subset + PDF facts set).

Everything comes from GitHub at a pinned commit or release tag and is checked
against the sha256 in the committed manifests before it lands in
``bench/data/extract_eval/`` (git-ignored). The data is never committed.

Usage:
    uv run python -m bench.extract_eval.fetch                   # fetch + verify
    uv run python -m bench.extract_eval.fetch --build-manifest  # maintainer only

``--build-manifest`` rewrites ``wcxb_manifest.json`` from the upstream
``metadata.json`` (trust on first use, then pinned). It also assigns the fixed
dev/test split, which must never change after the first measurement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from bench.extract_eval.download import ensure_file, fetch_bytes, sha256_bytes

HERE = Path(__file__).parent
DATA_DIR = Path("bench/data/extract_eval")
WCXB_MANIFEST = HERE / "wcxb_manifest.json"
PDF_FACTS = HERE / "pdf_facts.yaml"

WCXB_REPO = "Murrough-Foley/web-content-extraction-benchmark"
WCXB_COMMIT = "c039d5ee9f5a3a984a0e167e63aacd04e76e78a9"
WCXB_RAW = f"https://raw.githubusercontent.com/{WCXB_REPO}/{WCXB_COMMIT}/"
WCXB_SOURCE = {
    "name": "WCXB: Web Content Extraction Benchmark v1.0 (documentation subset)",
    "repo": f"https://github.com/{WCXB_REPO}",
    "commit": WCXB_COMMIT,
    "license": "CC-BY-4.0",
    "attribution": (
        "WCXB by Murrough Foley (2026), https://github.com/" + WCXB_REPO + ", "
        "licensed CC-BY-4.0. Used unmodified for evaluation; not redistributed "
        "here (only ids, URLs and checksums are committed)."
    ),
    "citation": "Foley, M. (2026). WCXB: A Multi-Type Web Content Extraction "
    "Benchmark. arXiv:2605.21097.",
}
SPLIT_RULE = (
    "int(sha256(page_id as UTF-8).hexdigest(), 16) % 2: 0 -> dev, 1 -> test. "
    "Fixed on 2026-10-28 before any measurement with this harness."
)


def split_for(page_id: str) -> str:
    """The fixed dev/test split of a WCXB page id (never tuned, never changed)."""
    digest = int(hashlib.sha256(page_id.encode()).hexdigest(), 16)
    return "dev" if digest % 2 == 0 else "test"


def wcxb_paths(page: dict[str, Any]) -> tuple[str, str]:
    """Upstream relative paths of a page's HTML and ground truth."""
    up = page["upstream_split"]
    return f"{up}/html/{page['id']}.html.gz", f"{up}/ground-truth/{page['id']}.json"


def wcxb_local(page: dict[str, Any]) -> tuple[Path, Path]:
    html, gt = wcxb_paths(page)
    return DATA_DIR / "wcxb" / html, DATA_DIR / "wcxb" / gt


def load_wcxb_manifest() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(WCXB_MANIFEST.read_text(encoding="utf-8"))
    return data


def load_pdf_facts() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(PDF_FACTS.read_text(encoding="utf-8"))
    return data


def pdf_local(doc: dict[str, Any]) -> Path:
    return DATA_DIR / "pdf" / f"{doc['id']}.pdf"


def build_manifest() -> dict[str, Any]:  # pragma: no cover - network
    """Pin the documentation pages of WCXB at ``WCXB_COMMIT``."""
    meta_raw = fetch_bytes(WCXB_RAW + "metadata.json")
    license_raw = fetch_bytes(WCXB_RAW + "LICENSE")
    meta = json.loads(meta_raw)
    if meta.get("license") != "CC-BY-4.0":
        raise SystemExit(f"unexpected WCXB license: {meta.get('license')!r}")
    pages = []
    for page_id, info in sorted(meta["files"].items()):
        if info.get("page_type") != "documentation":
            continue
        page = {"id": page_id, "upstream_split": info["split"]}
        html_rel, gt_rel = wcxb_paths(page)
        html = fetch_bytes(WCXB_RAW + html_rel)
        gt = fetch_bytes(WCXB_RAW + gt_rel)
        gt_doc = json.loads(gt)
        pages.append(
            {
                "id": page_id,
                "url": gt_doc.get("url", ""),
                "domain": info.get("domain", ""),
                "upstream_split": info["split"],
                "split": split_for(page_id),
                "html_sha256": sha256_bytes(html),
                "gt_sha256": sha256_bytes(gt),
            }
        )
        print(f"pinned {page_id} {pages[-1]['split']}", file=sys.stderr)
    return {
        "source": WCXB_SOURCE,
        "split_rule": SPLIT_RULE,
        "files": {
            "metadata.json": sha256_bytes(meta_raw),
            "LICENSE": sha256_bytes(license_raw),
        },
        "counts": {
            "pages": len(pages),
            "dev": sum(p["split"] == "dev" for p in pages),
            "test": sum(p["split"] == "test" for p in pages),
        },
        "pages": pages,
    }


def fetch_all() -> tuple[int, int]:  # pragma: no cover - network
    """Fetch and verify every pinned file; return (downloaded, already_ok)."""
    downloaded = kept = 0

    def get(url: str, dest: Path, sha: str) -> None:
        nonlocal downloaded, kept
        if ensure_file(url, dest, sha):
            downloaded += 1
        else:
            kept += 1

    manifest = load_wcxb_manifest()
    for name, sha in manifest["files"].items():
        get(WCXB_RAW + name, DATA_DIR / "wcxb" / name, sha)
    for page in manifest["pages"]:
        html_rel, gt_rel = wcxb_paths(page)
        html_dest, gt_dest = wcxb_local(page)
        get(WCXB_RAW + html_rel, html_dest, page["html_sha256"])
        get(WCXB_RAW + gt_rel, gt_dest, page["gt_sha256"])
    for doc in load_pdf_facts()["documents"]:
        get(doc["download_url"], pdf_local(doc), doc["sha256"])
        if doc.get("license_url"):
            lic = DATA_DIR / "pdf" / "licenses" / f"{doc['id']}.LICENSE"
            get(doc["license_url"], lic, doc["license_sha256"])
    return downloaded, kept


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - network
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build-manifest", action="store_true")
    args = parser.parse_args(argv)
    if args.build_manifest:
        manifest = build_manifest()
        text = json.dumps(manifest, indent=1) + "\n"
        WCXB_MANIFEST.write_text(text, encoding="utf-8")
        print(f"wrote {WCXB_MANIFEST} ({manifest['counts']})")
        return 0
    downloaded, kept = fetch_all()
    print(f"extract-eval data: {downloaded} downloaded, {kept} already verified")
    print("paid calls: 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
