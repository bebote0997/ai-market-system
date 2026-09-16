from dataclasses import asdict
from typing import Protocol, Sequence

from core.contracts import MacroEvent, NewsItem


class MacroNewsProvider(Protocol):
    def macro_events(self) -> Sequence[MacroEvent]: ...
    def news_items(self) -> Sequence[NewsItem]: ...


class InMemoryMacroNewsProvider:
    def __init__(self, events=(), news=(), error=None):
        self._events = tuple(events)
        self._news = tuple(news)
        self._error = error

    def macro_events(self):
        if self._error is not None:
            raise self._error
        return self._events

    def news_items(self):
        if self._error is not None:
            raise self._error
        return self._news


def macro_event_to_dict(event):
    return asdict(event) if isinstance(event, MacroEvent) else dict(event)


def news_item_to_dict(item):
    return asdict(item) if isinstance(item, NewsItem) else dict(item)
