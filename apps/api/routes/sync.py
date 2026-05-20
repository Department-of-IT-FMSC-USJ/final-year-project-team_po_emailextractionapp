from fastapi import APIRouter, Query

router = APIRouter()


@router.post("")
def trigger_sync(user_id: str = Query(..., description="Connected Outlook user id (Azure oid)")):
    from workers.queue import default_queue
    from workers.tasks import sync_inbox

    job = default_queue.enqueue(sync_inbox, user_id)
    return {"job_id": job.id, "message": "Inbox sync started", "user_id": user_id}
