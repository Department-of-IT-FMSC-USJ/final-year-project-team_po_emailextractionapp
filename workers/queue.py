from redis import Redis
from rq import Queue

from config.settings import settings

_redis = Redis.from_url(settings.redis_url)
default_queue = Queue("default", connection=_redis)


def enqueue_classify(email_id: str, subject: str, body_text: str):
    from workers.tasks import classify_email

    return default_queue.enqueue(classify_email, email_id, subject, body_text)


def enqueue_extract(email_id: str, body_text: str, attachment_paths: list[str]):
    from workers.tasks import extract_email

    return default_queue.enqueue(extract_email, email_id, body_text, attachment_paths)


def enqueue_process(email_id: str):
    from workers.tasks import process_email

    return default_queue.enqueue(process_email, email_id)
