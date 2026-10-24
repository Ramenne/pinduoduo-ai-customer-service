from __future__ import annotations

import uvicorn

from .app import create_app
from .config import Settings


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(
        create_app(settings),
        host=settings.bind_host,
        port=settings.bind_port,
        access_log=True,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        ws_max_size=settings.max_result_bytes,
        ws_ping_interval=20,
        ws_ping_timeout=10,
        ws_per_message_deflate=False,
        timeout_keep_alive=5,
    )


if __name__ == "__main__":
    main()
