"""Offline tests for the M10 Stack Overflow dump miner (bench/mine_dump.py)."""

from __future__ import annotations

import io

from lxml import etree

from bench.mine_dump import (
    MiningStats,
    cited_doc_urls,
    html_to_text,
    iter_rows,
    mine_posts,
    site_for_url,
    task_type,
    user_names,
)

_DOC_LINK = '<a href="https://docs.python.org/3/library/functions.html#print">doc</a>'
_ANSWER = f"<p>Use end=, see {_DOC_LINK}</p>"


def _xml(rows: list[dict[str, str]]) -> io.BytesIO:
    root = etree.Element("posts")
    for row in rows:
        etree.SubElement(root, "row", attrib=row)
    return io.BytesIO(etree.tostring(root, xml_declaration=True, encoding="utf-8"))


def _q(qid: int, answer: int | None, **extra: str) -> dict[str, str]:
    row = {
        "Id": str(qid),
        "PostTypeId": "1",
        "CreationDate": "2023-03-01T10:00:00.000",
        "Score": "2",
        "Title": f"question {qid}",
        "Body": "<p>Why does print add a newline?</p>",
        "OwnerUserId": "7",
    }
    if answer is not None:
        row["AcceptedAnswerId"] = str(answer)
    return row | extra


def _a(aid: int, body: str = _ANSWER, **extra: str) -> dict[str, str]:
    row = {
        "Id": str(aid),
        "PostTypeId": "2",
        "CreationDate": "2023-03-01T11:00:00.000",
        "Score": "3",
        "Body": body,
        "OwnerUserId": "8",
    }
    return row | extra


def test_mine_posts_applies_rules_in_prereg_order() -> None:
    old = "2022-05-01T00:00:00.000"
    rows = [
        _q(1, 2, CreationDate=old),  # before the window: not rule 1
        _a(2, CreationDate=old),
        _q(3, None),  # no accepted answer: not rule 1
        _q(10, 11),  # kept
        _a(11, _ANSWER + "<blockquote><p>end defaults to\n a newline</p></blockquote>"),
        _a(12),  # not the accepted answer: ignored
        _q(20, 21),
        _a(21, '<a href="https://stackoverflow.com/q/1">dup</a>'),  # rule 2
        _q(30, 31, Score="0"),
        _a(31),  # rule 3: question score
        _q(40, 41, ClosedDate="2023-04-01T00:00:00.000"),
        _a(41),  # rule 3: closed
        _q(50, 51),
        _a(51, Score="0"),  # rule 3: answer score
        _q(60, 61, Body='<p>see</p><img src="x.png">'),
        _a(61),  # rule 4: image
        _q(70, 71, Body="<p>" + "word " * 2100 + "</p>"),
        _a(71),  # rule 4: too long
        _q(80, 81),  # accepted answer never appears
    ]
    stats = MiningStats()
    kept = list(mine_posts(iter_rows(_xml(rows)), stats))

    assert (stats.questions_seen, stats.rule1_kept) == (10, 8)
    assert stats.removed_rule2_no_doc_link == 1
    assert stats.removed_rule3_score_or_closed == 3
    assert stats.removed_rule4_image_or_length == 2
    assert (stats.accepted_answer_missing, stats.kept) == (1, 1)
    assert stats.newest_post == "2023-03-01T11:00:00.000"
    (c,) = kept
    assert (c["question_id"], c["answer_id"], c["site"]) == (10, 11, "python")
    assert c["cited_urls"] == ["https://docs.python.org/3/library/functions.html"]
    assert c["answer_blockquotes"] == ["end defaults to a newline"]
    assert c["question_text"] == "Why does print add a newline?"
    assert c["task_type"] == "fact"
    assert c["question_url"] == "https://stackoverflow.com/q/10"
    assert (c["question_owner_id"], c["answer_owner_id"]) == (7, 8)


def test_site_for_url_respects_hosts_and_path_prefixes() -> None:
    assert site_for_url("https://docs.python.org/3/library/re.html") == "python"
    assert site_for_url("https://www.postgresql.org/docs/16/sql-select.html") == (
        "postgresql"
    )
    assert site_for_url("https://www.postgresql.org/about/") is None
    assert site_for_url("https://kubernetes.io/zh-cn/docs/home/") is None
    assert site_for_url("https://datatracker.ietf.org/doc/html/rfc9110") == "rfc"
    assert site_for_url("https://example.com/docs/") is None
    assert site_for_url("mailto:someone@docs.python.org") is None


def test_cited_doc_urls_merges_sections_and_keeps_link_order() -> None:
    html = (
        '<a href="http://docs.python.org/3/x.html#a">1</a>'
        '<a href="https://docs.python.org/3/x.html#b">2</a>'
        '<a href="https://developer.mozilla.org/en-US/docs/Web">3</a>'
        '<a href="https://example.com/">4</a>'
    )
    assert cited_doc_urls(html) == [
        ("python", "https://docs.python.org/3/x.html"),
        ("mdn", "https://developer.mozilla.org/en-US/docs/Web"),
    ]


def test_task_type_first_matching_rule_wins() -> None:
    assert task_type(2, "deprecated in 3.12", "", "<pre>x</pre>") == "multi_doc"
    assert task_type(1, "how do I migrate to v2?", "", "<pre>x</pre>") == "version"
    assert task_type(1, "how?", "Changed in version 3.11", "") == "version"
    assert task_type(1, "how?", "use it", "<pre><code>x</code></pre>") == "code"
    assert task_type(1, "how?", "use it", "<p>use it</p>") == "fact"


def test_html_to_text_keeps_code_layout() -> None:
    text = html_to_text("<p>Try:</p><pre><code>a = 1\nb = 2\n</code></pre><p>done</p>")
    assert "Try:\n" in text
    assert "a = 1\nb = 2" in text
    assert text.endswith("done")
    assert html_to_text("<!-- only a comment -->") == ""


def test_user_names_returns_only_requested_ids() -> None:
    root = etree.Element("users")
    for uid, name in (("7", "Ada"), ("8", "Linus"), ("9", "Grace")):
        etree.SubElement(root, "row", attrib={"Id": uid, "DisplayName": name})
    stream = io.BytesIO(etree.tostring(root))
    assert user_names(iter_rows(stream), {7, 9}) == {7: "Ada", 9: "Grace"}
