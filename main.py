"""Application entrypoint used by the systemd service."""

from __future__ import annotations

if __package__ in {None, ""}:  # pragma: no cover - script execution support
    import sys
    from pathlib import Path

    sys.path.append(str(Path(__file__).resolve().parent.parent))

from tastytrade_autotrader.config import get_config
from tastytrade_autotrader.server.flask_server import create_app
from tastytrade_autotrader.utils.logger import get_logger


def main() -> None:
    """Start the Flask trading service."""
    config = get_config()
    logger = get_logger(__name__)
    app = create_app(config=config)
    logger.info(
        "Starting autotrader service on %s:%s",
        config.flask_host,
        config.flask_port,
    )
    app.run(host=config.flask_host, port=config.flask_port)


if __name__ == "__main__":  # pragma: no cover
    main()
