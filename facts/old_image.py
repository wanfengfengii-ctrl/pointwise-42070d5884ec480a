from dataclasses import dataclass

from batches.rows import validate_reading
from facts.provenance import FactSource
from limits.validation import require_text, validate_station_day
from storage import Conflict


@dataclass(frozen=True)
class OldImage:
    station: str
    day: str
    key: str
    reading: int | None
    source: FactSource

    @classmethod
    def from_fact(cls, fact):
        return cls(fact.station, fact.day, fact.key, fact.reading, fact.source)


def validate_old_image(image):
    if not isinstance(image, OldImage) or not isinstance(image.source, FactSource):
        raise ValueError('a complete old image and source are required')
    validate_station_day(image.station, image.day)
    require_text(image.key, 'old observation key')
    validate_reading(image.reading)
    require_text(image.source.batch_id, 'old source batch id')
    if type(image.source.slot) is not int or image.source.slot < 0:
        raise ValueError('old source slot must be a nonnegative integer')
    if image.source.correction_id is not None:
        require_text(image.source.correction_id, 'old source correction id')
    if image.source.label_id is not None:
        require_text(image.source.label_id, 'old source label id')


def require_matching_old_image(current, image):
    validate_old_image(image)
    if current is None or OldImage.from_fact(current) != image:
        raise Conflict('old image does not match the current observation')
