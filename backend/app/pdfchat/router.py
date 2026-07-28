"""Chat-with-PDFs endpoints: document list, cited answer, raw PDF, reindex."""
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.auth.jwt import CurrentUser, get_current_user
from app.pdfchat import service
from app.pdfchat.render import render_page_png
from app.pdfchat.source import get_pdf_source
from app.rate_limit import limiter

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
    return service.answer(body.message)


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
