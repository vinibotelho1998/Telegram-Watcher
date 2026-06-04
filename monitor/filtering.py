from __future__ import annotations

from collections.abc import Iterable

from .detector import Detection, detect, matches_smart_rules
from .matching import matches_exclude, matches_include
from .models import Filter


def resolve_destinations(
    filters: list[Filter],
    chat_id: int,
    text: str,
    default_destinations: Iterable[int],
) -> list[int]:
    defaults = list(dict.fromkeys(default_destinations))
    if not any(f.enabled for f in filters):
        return defaults

    active = [f for f in filters if f.enabled and f.applies_to(chat_id)]
    targets: list[int] = []
    detection: Detection | None = None

    for flt in active:
        if matches_exclude(text, flt.exclude):
            continue
        if flt.smart_rules:
            if detection is None:
                detection = detect(text)
            if detection.confidence < flt.min_confidence:
                continue
            if not matches_smart_rules(detection, flt.smart_rules):
                continue
        if not matches_include(text, flt.include):
            continue
        for dest in (flt.destination_ids or defaults):
            if dest not in targets:
                targets.append(dest)

    return targets
