"""Domain pack registry.

Exactly one pack is active per process, chosen by the DOMAIN setting. Resolution
of table names and the governance-doc path lives here because it needs Settings;
the packs themselves are plain data (see base.py).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.domains.base import DomainPack

DOMAINS_DIR = Path(__file__).resolve().parent


@lru_cache
def available_domains() -> tuple[str, ...]:
    return ("abdm", "pmjay")


@lru_cache
def get_domain(key: str | None = None) -> DomainPack:
    """The active pack. Defaults to the DOMAIN setting.

    Raises on an unknown key rather than silently falling back: serving ABDM
    rules against PM-JAY tables would produce confidently wrong SQL, and the PII
    list would be the wrong one.
    """
    if key is None:
        from app.config import get_settings

        key = get_settings().domain
    key = (key or "").strip().lower()
    if key == "abdm":
        from app.domains.abdm import PACK

        return PACK
    if key == "pmjay":
        from app.domains.pmjay import PACK

        return PACK
    raise ValueError(
        f"Unknown DOMAIN {key!r}. Expected one of: {', '.join(available_domains())}."
    )


def table_name(key: str) -> str:
    """Bare BigQuery table name for a logical table key.

    An explicit `BQ_{KEY}_TABLE` setting wins over the pack default, so an
    existing deployment's env (BQ_FACILITY_REGISTRY_TABLE, ...) keeps working
    unchanged. Read through Settings, never os.getenv — see commit a7be906.
    """
    from app.config import get_settings

    pack = get_domain()
    if key not in pack.tables:
        raise KeyError(
            f"{key!r} is not a table in the {pack.key!r} domain. "
            f"Known: {', '.join(pack.table_keys)}."
        )
    override = getattr(get_settings(), f"bq_{key}_table", None)
    return (override or "").strip() or pack.tables[key]


def table_ref(key: str) -> str:
    """Fully-qualified, backtick-quoted table reference."""
    from app.config import get_settings

    cfg = get_settings()
    return f"`{cfg.gcp_project}.{cfg.bq_dataset}.{table_name(key)}`"


def table_map() -> dict[str, str]:
    """Logical key -> resolved bare table name, for every table in the domain."""
    return {k: table_name(k) for k in get_domain().table_keys}


def governance_path() -> Path:
    pack = get_domain()
    return DOMAINS_DIR / pack.key / pack.governance_file
