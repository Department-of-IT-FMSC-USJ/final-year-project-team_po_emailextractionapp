from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from domain.schemas import EmailDetail, EmailSummary
from storage.db.session import get_db_session

router = APIRouter()


@router.get("", response_model=list[EmailSummary])
def list_emails(
    status: str | None = None,
    db: Session = Depends(get_db_session),
):
    _ = db, status
    return []


@router.get("/{email_id}", response_model=EmailDetail)
def get_email(email_id: str, db: Session = Depends(get_db_session)):
    _ = db
    raise HTTPException(status_code=404, detail="Email not found")


@router.post("/{email_id}/classify")
def trigger_classify(email_id: str):
    from workers.queue import enqueue_classify

    # TODO: load subject/body from DB
    job = enqueue_classify(email_id, "", "")
    return {"job_id": job.id, "step": "classify"}


@router.post("/{email_id}/extract")
def trigger_extract(email_id: str):
    from workers.queue import enqueue_extract

    job = enqueue_extract(email_id, "", [])
    return {"job_id": job.id, "step": "extract"}


@router.post("/{email_id}/process")
def trigger_process(email_id: str):
    from workers.queue import enqueue_process

    job = enqueue_process(email_id)
    return {"job_id": job.id, "step": "classify_then_extract"}
