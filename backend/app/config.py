"""Central configuration, loaded from environment / .env.

Every external coordinate (GCP project, dataset, table names, credentials,
OpenAI key/model, JWT secret) lives here so nothing is hardcoded elsewhere.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ directory (this file is backend/app/config.py)
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # BigQuery
    gcp_project: str = "nha-conversational-analytics"
    bq_dataset: str = "nha_conversational_analytics"
    # ABDM digital-adoption tables (no merged table — joined by facility ID /
    # geography, see GOVERNANCE.md §10). Override any of these via the env if the
    # loaded table names differ.
    bq_facility_registry_table: str = "health_facility_registry"
    bq_professionals_registry_table: str = "health_professionals_registry"
    bq_top_indicators_table: str = "healthid_top_indicators"
    bq_linked_trend_table: str = "healthid_linked_trend"
    bq_linked_facility_table: str = "linked_facility"
    bq_scan_share_table: str = "scan_and_share"
    bq_scan_pay_table: str = "scan_pay_count"
    bq_state_district_master_table: str = "state_district_master"
    bq_bridge_integrator_table: str = "integrator_detail"
    # Auth to BigQuery — provide EITHER of these (inline JSON takes precedence):
    #   google_credentials_json : the full service-account key JSON, inline
    #   google_application_credentials : a path to the key file
    google_credentials_json: str = ""
    google_application_credentials: str = ""
    bq_max_bytes_billed: int = 2_147_483_648  # 2 GB

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1"
    openai_embedding_model: str = "text-embedding-3-small"
    # A (typically stronger) model for the Explorer's idea generation. Falls back
    # to openai_model when unset. e.g. "gpt-4.1" or a reasoning model.
    openai_explorer_model: str = ""

    # Auth
    jwt_secret: str = "change-me-to-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480
    # The auth cookie is Secure (HTTPS-only) by default. Set COOKIE_SECURE=false
    # ONLY for local development over plain http://localhost, where the browser
    # would otherwise refuse to store it.
    cookie_secure: bool = True
    # Downgrades the startup refusal over a default JWT_SECRET / missing
    # APP_USERS to a warning. Declared here (rather than read with os.getenv)
    # so it works from backend/.env as well as from a real environment
    # variable — .env is what the setup instructions actually tell you to edit,
    # and reading the process environment alone silently ignored it.
    allow_insecure_dev: bool = False
    # Production users, set via env to override the dev seed accounts. Format:
    #   APP_USERS="user1:password1:role1;user2:password2:role2"
    # Roles: viewer | analyst | senior_analyst | admin. Empty = dev seed users.
    app_users: str = ""

    # App
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Folder holding lgd_master.xlsx (geography). Bundled in backend/reference
    # so a fresh clone is self-contained. Resolved relative to backend/.
    reference_data_dir: str = "reference"
    # Chat-with-PDFs source: "local" (a folder on disk) or "drive" (a Google Drive
    # folder). Drive uses the same GOOGLE_CREDENTIALS_JSON service account; share
    # the Drive folder with that service account's email and set pdf_drive_folder_id.
    pdf_source: str = "local"
    pdf_drive_folder_id: str = ""
    # Local source folder (used when pdf_source=local). Resolved relative to backend/.
    pdf_dir: str = "pdfs"
    # Where the built PDF index (chunks + embeddings) is cached.
    pdf_index_dir: str = "pdf_index"
    # OCR (for scanned/image PDFs with no text layer).
    # ocr_engine: which backend renders text from page images:
    #   "tesseract" (default) — local binary, no cloud/billing, works offline.
    #   "vision"    — Google Cloud Vision (DOCUMENT_TEXT_DETECTION); needs the
    #                 Vision API enabled + billing on GCP_PROJECT and valid
    #                 GOOGLE_APPLICATION_CREDENTIALS. Better on noisy scans, no
    #                 binary to install (ideal for the GCP deployment).
    #   "auto"      — use Vision if it is importable and credentials look present,
    #                 else fall back to tesseract.
    # If the chosen engine is unavailable at runtime it falls back gracefully
    # (vision -> tesseract -> none) so ingestion never hard-fails.
    ocr_engine: str = "tesseract"
    # Tesseract binary path — if empty, common locations + PATH are auto-detected.
    tesseract_cmd: str = ""
    # DPI used to render pages before OCR (both engines). Trades speed vs accuracy.
    ocr_dpi: int = 220
    # Optional BCP-47 language hints for Vision, comma-separated (e.g. "en,hi").
    # Empty lets Vision auto-detect.
    vision_language_hints: str = ""

    # ----- derived helpers -----
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def reference_dir(self) -> Path:
        p = Path(self.reference_data_dir)
        if not p.is_absolute():
            p = (BACKEND_DIR / p).resolve()
        return p

    def _resolve(self, raw: str) -> Path:
        p = Path(raw)
        return p if p.is_absolute() else (BACKEND_DIR / p).resolve()

    @property
    def pdf_dir_path(self) -> Path:
        return self._resolve(self.pdf_dir)

    @property
    def pdf_index_path(self) -> Path:
        return self._resolve(self.pdf_index_dir)

    # Maps the GOVERNANCE.md placeholder keys to the configured table names. The keys
    # here match the {..._TABLE} placeholders substituted in prompt_builder.
    @property
    def table_map(self) -> dict[str, str]:
        return {
            "facility_registry": self.bq_facility_registry_table,
            "professionals_registry": self.bq_professionals_registry_table,
            "top_indicators": self.bq_top_indicators_table,
            "linked_trend": self.bq_linked_trend_table,
            "linked_facility": self.bq_linked_facility_table,
            "scan_share": self.bq_scan_share_table,
            "scan_pay": self.bq_scan_pay_table,
            "state_district_master": self.bq_state_district_master_table,
            "bridge_integrator": self.bq_bridge_integrator_table,
        }

    def table_ref(self, which: str) -> str:
        """Fully qualified, backtick-quoted BigQuery table reference for a
        table_map key (e.g. 'facility_registry')."""
        table = self.table_map.get(which, self.bq_facility_registry_table)
        return f"`{self.gcp_project}.{self.bq_dataset}.{table}`"


@lru_cache
def get_settings() -> Settings:
    return Settings()
