"""Chat-with-PDFs endpoints: document list, cited answer, raw PDF, reindex."""
import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.auth.jwt import CurrentUser, get_current_user
from app.pdfchat import service
from app.pdfchat.render import render_page_png
from app.pdfchat.source import get_pdf_source
from app.query_log.logger import log_query
from app.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pdfchat", tags=["pdfchat"])


class PdfChatRequest(BaseModel):
    message: str


@router.get("/documents")
def documents(user: CurrentUser = Depends(get_current_user)):
    return {"documents": service.list_documents()}


@router.post("/message")
@limiter.limit("30/minute")
def message(
    request: Request,
    body: PdfChatRequest = Body(...),
    user: CurrentUser = Depends(get_current_user),
):
    result = service.answer(body.message)
    # PDF chat previously bypassed the audit trail entirely. It generates no
    # SQL, so generated_sql stays null; what matters here is who asked what and
    # which documents were cited.
    try:
        cites = result.get("citations") or []
        log_query(
            session_id=f"pdfchat:{user.username}",
            user_id=user.username,
            user_role=user.role,
            original_question=body.message,
            resolved_geography=None,
            resolved_period=None,
            generated_sql=None,
            execution_status="success" if result.get("found") else "not_found",
            error_message=None,
            row_count=len(cites),
            response_shown=result.get("answer"),
            source="pdfchat",
        )
    except Exception:  # noqa: BLE001 - logging must never break an answer
        logger.warning("PDF chat query log failed", exc_info=True)
    return result


@router.get("/file/{pdf_id}")
def file(pdf_id: str, user: CurrentUser = Depends(get_current_user)):
    try:
        data = get_pdf_source().read_bytes(pdf_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="PDF not found")
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{pdf_id}.pdf"'},
    )


@router.get("/page/{pdf_id}/{page}")
def page_image(pdf_id: str, page: int, user: CurrentUser = Depends(get_current_user)):
    try:
        png = render_page_png(pdf_id, page)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="Page not found")
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.post("/reindex")
def reindex(user: CurrentUser = Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    idx = service.get_index(force=True)
    return {"status": "ok", "chunks": len(idx.chunks), "documents": len(service.list_documents())}
