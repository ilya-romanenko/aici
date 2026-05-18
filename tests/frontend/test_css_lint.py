import re
from pathlib import Path

CSS_DIR = Path(__file__).resolve().parents[2] / "src" / "ai_crypto_index" / "frontend" / "static" / "css"
ALLOWLIST = {"normalize.css"}

COMMENT_PATTERN = re.compile(r"/\*.*?\*/", re.DOTALL)
TYPE_SELECTOR_PATTERN = re.compile(r"[.#\w-]*\s*\[type\s*=\s*['\"]?[\w-]+['\"]?\]", re.IGNORECASE)


def _collect_type_selectors(css_text: str):
    comment_spans = [(match.start(), match.end()) for match in COMMENT_PATTERN.finditer(css_text)]
    for match in TYPE_SELECTOR_PATTERN.finditer(css_text):
        start = match.start()
        if any(span_start <= start < span_end for span_start, span_end in comment_spans):
            continue
        yield start, match.group(0).strip()


def test_common_form_styles_avoid_type_attribute_selectors():
    offenders = []

    for css_path in CSS_DIR.glob("*.css"):
        if css_path.name in ALLOWLIST:
            continue

        css_text = css_path.read_text(encoding="utf-8")
        for offset, selector in _collect_type_selectors(css_text):
            line = css_text.count("\n", 0, offset) + 1
            relative = css_path.relative_to(CSS_DIR)
            offenders.append(f"{relative}:{line} -> {selector}")

    assert not offenders, (
        "Avoid narrow [type=...] selectors in shared form styles; "
        "prefer class-based hooks (e.g., .form__field--email) instead.\n"
        + "\n".join(offenders)
    )
