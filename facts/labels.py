from collections.abc import Sequence
from dataclasses import dataclass

from facts.label_repository import insert_label_request, load_label_request
from facts.old_image import OldImage, validate_old_image
from facts.provenance import source_after_relabel
from facts.repository import CurrentFact, load_current_fact, relabel_current_fact
from limits.validation import require_text
from storage import Conflict, transaction


@dataclass(frozen=True)
class LabelReceipt:
    request_id: str
    reason: str
    after: tuple[CurrentFact, ...]


def _normalize_changes(changes):
    if not isinstance(changes, Sequence) or isinstance(changes, (str, bytes, bytearray)):
        raise ValueError('label changes must be an ordered nonempty sequence')
    items = []
    for position, item in enumerate(changes):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError(f'label change {position} must contain an old image and a target key')
        old, new_key = item
        validate_old_image(old)
        require_text(new_key, f'target key at change {position}')
        items.append((old, new_key))
    if not items:
        raise ValueError('label changes must be an ordered nonempty sequence')
    return tuple(items)


def _validate_shape(items):
    station, day = items[0][0].station, items[0][0].day
    source_keys = []
    target_keys = []
    for old, new_key in items:
        if (old.station, old.day) != (station, day):
            raise ValueError('label changes must all belong to one station-day')
        if old.key == new_key:
            raise ValueError('a relabel must change the observation key')
        source_keys.append(old.key)
        target_keys.append(new_key)
    if len(set(source_keys)) != len(source_keys):
        raise ValueError('label change source keys must be distinct')
    if len(set(target_keys)) != len(target_keys):
        raise ValueError('label change target keys must be distinct')
    return station, day


def _receipt(request_id, reason, station, day, items):
    return LabelReceipt(
        request_id,
        reason,
        tuple(
            CurrentFact(station, day, new_key, old.reading, source_after_relabel(old.source, request_id))
            for old, new_key in items
        ),
    )


def _replay(db, request_id, reason, items):
    stored = load_label_request(db, request_id)
    if stored is None:
        return None
    if stored.reason != reason or stored.items != items:
        raise Conflict('label request id is already bound to another request')
    return _receipt(request_id, reason, stored.station, stored.day, stored.items)


def _placeholder(request_id, position):
    # Valid observation keys never contain NUL, so this can never collide with one.
    return f'\x00relabel\x00{request_id}\x00{position}'


def _apply(db, station, day, items, request_id):
    # Two phases through NUL placeholders allow swaps and closed cycles inside
    # one request while holding every source and target key against the same
    # pre-commit image.
    for position, (old, _) in enumerate(items):
        relabel_current_fact(db, station, day, old.key, _placeholder(request_id, position), request_id)
    for position, (old, new_key) in enumerate(items):
        relabel_current_fact(
            db, station, day, _placeholder(request_id, position), new_key, request_id
        )


def relabel_facts(db, request_id, reason, changes):
    require_text(request_id, 'label request id')
    require_text(reason, 'label reason')
    items = _normalize_changes(changes)
    station, day = _validate_shape(items)
    with transaction(db, write=True):
        replay = _replay(db, request_id, reason, items)
        if replay is not None:
            return replay

        # Complete old images and current occupancy are read from one pre-commit image.
        for old, _ in items:
            current = load_current_fact(db, station, day, old.key)
            if current is None or OldImage.from_fact(current) != old:
                raise Conflict(f'old image for {old.key!r} does not match the current observation')

        source_keys = {old.key for old, _ in items}
        for _, new_key in items:
            if new_key in source_keys:
                continue
            if load_current_fact(db, station, day, new_key) is not None:
                raise Conflict(f'target key {new_key!r} is occupied outside the request')

        _apply(db, station, day, items, request_id)
        insert_label_request(db, request_id, station, day, reason, items)
        return _receipt(request_id, reason, station, day, items)
