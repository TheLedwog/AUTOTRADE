"""Unit tests for persisted order-history storage."""

from pathlib import Path
from uuid import uuid4

from tastytrade_autotrader.utils.order_history import OrderHistoryStore


def test_order_history_store_appends_and_reads_recent_entries():
    """Order history should be persisted in append order and returned from the end."""
    history_path = (
        Path(__file__).resolve().parents[2]
        / ".test_artifacts"
        / f"order-history-{uuid4().hex}.jsonl"
    )
    history_path.parent.mkdir(parents=True, exist_ok=True)
    store = OrderHistoryStore(history_path, max_entries=3)

    try:
        store.append({"timestamp": "1", "order_id": "A"})
        store.append({"timestamp": "2", "order_id": "B"})
        store.append({"timestamp": "3", "order_id": "C"})
        store.append({"timestamp": "4", "order_id": "D"})

        recent = store.recent(limit=3)

        assert [entry["order_id"] for entry in recent] == ["B", "C", "D"]
    finally:
        history_path.unlink(missing_ok=True)
