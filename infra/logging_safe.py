import logging

logger = logging.getLogger("updownfx.infra")

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def log_event(event: str, **fields) -> None:
    # Never accept password/token fields into logs.
    blocked = {"password", "token", "access_token", "refresh_token", "authorization"}
    safe = {k: v for k, v in fields.items() if k.lower() not in blocked}
    parts = " ".join(f"{k}={v}" for k, v in safe.items())
    logger.info("%s %s", event, parts)
