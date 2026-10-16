"""B7: pages in GBK and Shift_JIS decode correctly (M10a track B).

Competitors' issues show garbled non-UTF-8 pages; many such pages declare their
charset only in ``<meta>``, not in the Content-Type header.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.extract.html import extract_html
from app.extract.ir import SourceInfo

_TEXT = {
    "gbk": "安装软件包并配置环境变量。运行数据库迁移以后再启动服务。",
    "shift_jis": (
        "パッケージをインストールして環境変数を設定します。"
        "サービスを起動する前に移行を実行します。"
    ),
}


def _source(content_type: str) -> SourceInfo:
    return SourceInfo(
        url="https://docs.example.com/guide",
        final_url="https://docs.example.com/guide",
        content_type=content_type,
        fetched_at=datetime(2026, 10, 17, tzinfo=UTC),
        etag=None,
        last_modified=None,
        content_hash="b" * 64,
        render_mode="http",
    )


def _page(codec: str, meta: str) -> bytes:
    body = "".join(f"<p>{_TEXT[codec]}</p>" for _ in range(6))
    html = (
        f"<html><head>{meta}<title>t</title></head>"
        f"<body><main>{body}</main></body></html>"
    )
    return html.encode(codec)


def _text(doc: object) -> str:
    return " ".join(b.text for b in doc.blocks)  # type: ignore[attr-defined]


@pytest.mark.parametrize("codec", ["gbk", "shift_jis"])
@pytest.mark.parametrize(
    ("content_type", "meta"),
    [
        ("text/html; charset={codec}", ""),
        ("text/html", '<meta charset="{codec}">'),
        (
            "text/html",
            '<meta http-equiv="Content-Type" content="text/html; charset={codec}">',
        ),
    ],
    ids=["header", "meta-charset", "meta-http-equiv"],
)
def test_non_utf8_page_decodes(codec: str, content_type: str, meta: str) -> None:
    raw = _page(codec, meta.format(codec=codec))
    doc = extract_html(raw, _source(content_type.format(codec=codec)))
    assert _TEXT[codec] in _text(doc)
