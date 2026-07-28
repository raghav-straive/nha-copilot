"""PDF source abstraction.

The rest of Chat-with-PDFs depends only on `PdfSource`, so the corpus can come
from a local folder now and from Google Drive later without touching ingestion,
indexing, or the API. A Drive-backed source only needs to implement the same
three methods (list ids, get bytes, a fingerprint for cache invalidation).
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings


@dataclass
class PdfRef:
    id: str          # stable id used in URLs and citations
    name: str        # display name
    fingerprint: str  # changes when the file content changes (for cache invalidation)


class PdfSource(ABC):
    @abstractmethod
    def list_pdfs(self) -> list[PdfRef]:
        ...

    @abstractmethod
    def read_bytes(self, pdf_id: str) -> bytes:
        ...

    @abstractmethod
    def get_local_path(self, pdf_id: str) -> str:
        """Return a local filesystem path to the PDF (downloading to a temp file
        for remote sources). Lets pdfplumber/pypdfium2 open the file lazily instead
        of holding the whole file in memory — essential for large scanned PDFs."""
        ...

    def corpus_fingerprint(self) -> str:
        """A single hash over the whole corpus — the index is rebuilt when it changes."""
        h = hashlib.sha256()
        for ref in sorted(self.list_pdfs(), key=lambda r: r.id):
            h.update(ref.id.encode())
            h.update(ref.fingerprint.encode())
        return h.hexdigest()[:16]


class LocalFolderSource(PdfSource):
    """PDFs from a local folder (config: PDF_DIR). id = filename stem-safe slug."""

    def __init__(self, folder: Path | None = None) -> None:
        self.folder = folder or get_settings().pdf_dir_path

    def _by_id(self) -> dict[str, Path]:
        out: dict[str, Path] = {}
        if not self.folder.exists():
            return out
        for p in sorted(self.folder.glob("*.pdf")):
            out[self._slug(p.name)] = p
        return out

    @staticmethod
    def _slug(name: str) -> str:
        stem = Path(name).stem
        return "".join(c if (c.isalnum() or c in "-_") else "-" for c in stem).strip("-").lower() or "doc"

    def list_pdfs(self) -> list[PdfRef]:
        refs: list[PdfRef] = []
        for pid, path in self._by_id().items():
            st = path.stat()
            fp = f"{st.st_size}:{int(st.st_mtime)}"
            refs.append(PdfRef(id=pid, name=path.name, fingerprint=fp))
        return refs

    def read_bytes(self, pdf_id: str) -> bytes:
        return Path(self.get_local_path(pdf_id)).read_bytes()

    def get_local_path(self, pdf_id: str) -> str:
        path = self._by_id().get(pdf_id)
        if not path:
            raise FileNotFoundError(f"No PDF with id {pdf_id!r}")
        return str(path)


class GoogleDriveSource(PdfSource):
    """PDFs from a Google Drive folder, read via the service account in
    GOOGLE_CREDENTIALS_JSON (share the folder with that SA's email). New/changed/
    removed files are detected via each file's modifiedTime (drives reindexing).
    Works with both My-Drive shared folders and Shared Drives.
    """

    SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

    def __init__(self, folder_id: str | None = None) -> None:
        s = get_settings()
        self.folder_id = folder_id or s.pdf_drive_folder_id
        self._service = None
        self._paths: dict[str, str] = {}  # pdf_id -> downloaded temp file path

    def _get_service(self):
        if self._service is None:
            import json as _json

            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            s = get_settings()
            inline = (s.google_credentials_json or "").strip()
            if inline:
                info = _json.loads(inline)
                creds = service_account.Credentials.from_service_account_info(info, scopes=self.SCOPES)
            elif s.google_application_credentials:
                creds = service_account.Credentials.from_service_account_file(
                    s.google_application_credentials, scopes=self.SCOPES
                )
            else:
                raise RuntimeError("No service-account credentials configured for Google Drive.")
            self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def list_pdfs(self) -> list[PdfRef]:
        if not self.folder_id:
            raise RuntimeError("PDF_DRIVE_FOLDER_ID is not set.")
        svc = self._get_service()
        q = f"'{self.folder_id}' in parents and mimeType='application/pdf' and trashed=false"
        refs: list[PdfRef] = []
        page_token = None
        while True:
            resp = (
                svc.files()
                .list(
                    q=q,
                    fields="nextPageToken, files(id, name, modifiedTime, size, md5Checksum)",
                    pageSize=1000,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    pageToken=page_token,
                )
                .execute()
            )
            for f in resp.get("files", []):
                fp = f.get("md5Checksum") or f"{f.get('size','')}:{f.get('modifiedTime','')}"
                refs.append(PdfRef(id=f["id"], name=f["name"], fingerprint=fp))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return refs

    def read_bytes(self, pdf_id: str) -> bytes:
        import pathlib

        return pathlib.Path(self.get_local_path(pdf_id)).read_bytes()

    def get_local_path(self, pdf_id: str) -> str:
        import os
        import tempfile

        from googleapiclient.http import MediaIoBaseDownload

        existing = self._paths.get(pdf_id)
        if existing and os.path.exists(existing):
            return existing
        svc = self._get_service()
        fd, path = tempfile.mkstemp(suffix=".pdf", prefix="pdfchat_")
        # Stream to disk in chunks so a large PDF never sits fully in memory.
        with os.fdopen(fd, "wb") as fh:
            request = svc.files().get_media(fileId=pdf_id, supportsAllDrives=True)
            downloader = MediaIoBaseDownload(fh, request, chunksize=8 * 1024 * 1024)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        self._paths[pdf_id] = path
        return path


_source: PdfSource | None = None


def get_pdf_source() -> PdfSource:
    global _source
    if _source is None:
        s = get_settings()
        _source = GoogleDriveSource() if s.pdf_source.lower() == "drive" else LocalFolderSource()
    return _source


def set_pdf_source(source: PdfSource) -> None:
    """Swap the source without touching the rest."""
    global _source
    _source = source
