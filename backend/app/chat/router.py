"""Chat endpoints: the main turn handler and session history."""
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.jwt import CurrentUser, get_current_user
from app.chat.session import get_session_store
from app.nl_to_sql.pipeline import run_turn
from app.query_log.logger import log_query
from app.rate_limit import limiter

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    action: str  # answer | clarify | out_of_scope | chat | error
    answer: str | None = None
    message: str | None = None
    sql: str | None = None
    columns: list[str] = []
    rows: list[dict] = []
    chart: dict | None = None
    options: list[str] = []
    questions: list[dict] = []
    analysis: dict | None = None
    context_chips: dict = {}


@router.post("/message", response_model=ChatResponse)
@limiter.limit("60/minute")
def chat_message(
    request: Request,
    body: ChatRequest = Body(...),
    user: CurrentUser = Depends(get_current_user),
):
    store = get_session_store()
    session = store.get_or_create(body.session_id, user.username, user.role)

    result = run_turn(
        question=body.message,
        role=user.role,
        session_context=session.confirmed_context or None,
        history=list(session.history),
    )

    # Persist confirmed context on a successful data answer.
    if result.action == "answer":
        store.update_context(session, result.resolved)

    shown = result.answer or result.message
    session.history.append(
        {"role": "user", "content": body.message}
    )
    session.history.append(
        {
            "role": "assistant",
            "content": shown,
            "action": result.action,
            "sql": result.sql,
            "columns": result.columns,
            "rows": result.rows,
            "context_chips": result.context_chips,
        }
    )
    # Sessions live in SQLite now, shared across workers — mutating the object
    # in memory is not enough, it has to be written back.
    store.save(session)

    # Query log (every turn).
    log_query(
        session_id=session.session_id,
        user_id=user.username,
        user_role=user.role,
        original_question=body.message,
        resolved_geography=result.resolved.get("geography"),
        resolved_period=result.resolved.get("period"),
        generated_sql=result.sql,
        execution_status=result.execution_status,
        error_message=result.error_message,
        row_count=len(result.rows) if result.action == "answer" else None,
        response_shown=shown,
    )

    return ChatResponse(
        session_id=session.session_id,
        action=result.action,
        answer=result.answer,
        message=result.message,
        sql=result.sql,
        columns=result.columns,
        rows=_jsonable(result.rows),
        chart=result.chart,
        options=result.options,
        questions=result.questions,
        analysis=result.analysis,
        context_chips=result.context_chips,
    )


@router.get("/session/{session_id}")
def get_session(session_id: str, user: CurrentUser = Depends(get_current_user)):
    store = get_session_store()
    session = store.get(session_id)
    if not session or session.user_id != user.username:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session.session_id,
        "confirmed_context": session.confirmed_context,
        "history": _jsonable(session.history),
    }


def _jsonable(obj):
    """Coerce BigQuery Row values (dates, Decimals) into JSON-safe types."""
    import datetime
    import decimal

    if isinstance(obj, list):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    return obj
