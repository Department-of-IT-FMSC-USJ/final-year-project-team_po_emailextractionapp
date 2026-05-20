"""Start RQ worker: python -m workers.run"""

from redis import Redis
from rq import Worker

from config.settings import settings


def main() -> None:
    redis_conn = Redis.from_url(settings.redis_url)
    worker = Worker(["default"], connection=redis_conn)
    worker.work()


if __name__ == "__main__":
    main()
