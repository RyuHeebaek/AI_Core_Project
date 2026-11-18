"""실행 이벤트 발행을 위한 Pub/Sub 이벤트 버스 (F3012)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable


@dataclass(frozen=True)
class AgentEvent:
    """메인 Agent와 서브 Agent 간 전달되는 표준 이벤트."""

    session_id: str
    run_id: str
    event_type: str
    agent_name: str
    node_name: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


Subscriber = Callable[[AgentEvent], None]


class EventBus:
    """구독자 실패나 미연결에 영향을 받지 않는 인프로세스 이벤트 버스."""

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []
        self._lock = Lock()

    def subscribe(self, handler: Subscriber) -> None:
        """이벤트 구독자를 등록한다."""
        with self._lock:
            if handler not in self._subscribers:
                self._subscribers.append(handler)

    def unsubscribe(self, handler: Subscriber) -> None:
        """구독자를 해제한다."""
        with self._lock:
            self._subscribers = [s for s in self._subscribers if s != handler]

    def clear(self) -> None:
        """모든 구독자를 제거한다 (테스트용)."""
        with self._lock:
            self._subscribers.clear()

    def publish(self, event: AgentEvent) -> None:
        """구독자에게 이벤트를 전파한다. 구독자 오류로 메인 흐름을 중단하지 않는다."""
        with self._lock:
            handlers = list(self._subscribers)
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                # F3013: 로깅/구독자 실패가 메인 대화 처리를 방해하면 안 된다.
                continue


event_bus = EventBus()
