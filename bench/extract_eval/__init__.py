"""Zero-cost extraction-quality harness (M10b, Phase P measuring stick).

``fetch`` downloads the pinned WCXB documentation subset and the PDF facts set
from GitHub into ``bench/data/extract_eval/`` (git-ignored) and verifies every
file against the committed sha256. ``run`` measures NINA's frozen extractors
against trafilatura and readability (HTML) and against hand-checked facts
(PDF). No provider, no paid call.
"""
