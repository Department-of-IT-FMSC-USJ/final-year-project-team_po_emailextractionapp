from fastapi import APIRouter

router = APIRouter()


@router.post("")
def trigger_sync():
    from workers.queue import default_queue
    from workers.tasks import sync_inbox

    # TODO: resolve current user_id from session
    job = default_queue.enqueue(sync_inbox, "default-user")
    return {"job_id": job.id, "message": "Inbox sync started"}
