"""The platform must not contain scheme-specific content.

The analysis prompt lived in nl_to_sql.pipeline as a module constant, naming the
scheme and its metrics. A platform module that tells the model which programme's
data it is looking at survives a domain change untouched, and then silently
mislabels every insight it produces. These tests make that class of leak fail
loudly.

The check targets STRING LITERALS the program actually uses — not comments and
not docstrings, which legitimately explain the domain boundary and reference the
sibling tool.
"""
import ast
from pathlib import Path

import pytest

from app.domains import available_domains, get_domain

APP = Path(__file__).resolve().parent.parent / "app"

# Platform packages: everything except app/domains/, which is where domain
# content belongs.
PLATFORM_DIRS = [
    "auth", "chat", "db", "explorer", "nl_to_sql", "pdfchat", "query_log",
    "report", "semantic", "sql_safety",
]

# Words that name a health scheme. Any of these inside a live string literal in
# platform code means domain content has leaked out of the pack.
SCHEME_WORDS = (
    "ayushman bharat",
    "pm-jay",
    "pmjay",
    "abdm",
    "digital mission",
)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """ids() of string constants that are docstrings, so they can be skipped."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                out.add(id(body[0].value))
    return out


def _live_strings(path: Path):
    """Yield (lineno, text) for every string literal that is not a docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
        ):
            yield node.lineno, node.value


@pytest.mark.parametrize("folder", PLATFORM_DIRS)
def test_no_scheme_name_in_platform_string_literals(folder):
    d = APP / folder
    if not d.exists():
        pytest.skip(f"{folder} not present")
    offenders = []
    for py in sorted(d.rglob("*.py")):
        for lineno, text in _live_strings(py):
            low = text.lower()
            for word in SCHEME_WORDS:
                if word in low:
                    offenders.append(
                        f"{py.relative_to(APP)}:{lineno}: {word!r} in {text[:70]!r}"
                    )
                    break
    assert not offenders, (
        "scheme-specific text found in a platform string literal; it belongs in "
        "app/domains/<domain>/:\n  " + "\n  ".join(offenders)
    )


def test_every_domain_supplies_all_three_prompts():
    """An empty prompt sends the model an empty system message, which fails
    silently rather than loudly."""
    for key in available_domains():
        pack = get_domain(key)
        for field in ("explorer_system", "report_system", "analysis_system"):
            value = getattr(pack, field)
            assert value and value.strip(), f"{key}.{field} is empty"
            assert len(value) > 100, f"{key}.{field} looks truncated"


def test_the_analysis_prompt_is_read_from_the_pack_not_hardcoded():
    from app.nl_to_sql import pipeline

    assert not hasattr(pipeline, "_ANALYSIS_SYSTEM"), (
        "the analysis prompt is back as a module constant in the platform"
    )
    assert pipeline._analysis_system() == get_domain().analysis_system


def test_the_governance_doc_lives_inside_the_pack():
    from app.domains import governance_path

    p = governance_path()
    assert p.exists(), f"missing governance doc at {p}"
    assert p.parent.name == get_domain().key
    assert "domains" in p.parts


def test_the_governance_doc_has_no_unsubstituted_placeholders():
    from app.nl_to_sql.prompt_builder import load_system_prompt

    prompt = load_system_prompt()
    pack = get_domain()
    for key in pack.table_keys:
        assert pack.placeholder(key) not in prompt, (
            f"{pack.placeholder(key)} was never substituted - the model would "
            f"emit it literally into SQL"
        )


def test_every_pack_field_is_actually_read_by_the_platform():
    """A field nobody reads is a second source of truth waiting to drift.

    `ui` was one: UI copy and coded-value maps were declared on the pack AND in
    frontend/src/domain.ts, and only the frontend copy was ever used — exactly
    the duplication the pack exists to remove.
    """
    import dataclasses

    from app.domains.base import DomainPack

    names = {f.name for f in dataclasses.fields(DomainPack)}
    assert "ui" not in names, (
        "`ui` is back on DomainPack. UI copy belongs in frontend/src/domain.ts; "
        "declaring it here too means two sources of truth for the same strings."
    )

    # Every remaining field must be referenced somewhere outside base.py.
    searchable = ""
    for py in sorted((APP).rglob("*.py")):
        if py.name == "base.py":
            continue
        searchable += py.read_text(encoding="utf-8")
    structural = {"key", "label", "tables", "governance_file"}  # used via helpers
    unread = [
        n for n in names - structural
        if n not in searchable
    ]
    assert not unread, f"DomainPack fields declared but never read: {unread}"


def test_an_unknown_domain_is_refused_rather_than_defaulted():
    """Falling back to a default pack would mean the wrong PII list and
    confidently wrong SQL against tables that do not exist."""
    with pytest.raises(ValueError, match="Unknown DOMAIN"):
        get_domain("not-a-real-scheme")
