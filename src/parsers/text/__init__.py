"""③-a TEXT PARSER — 헤더를 표준 컬럼에 붙인다"""
from .excel import TextParseResult, UnmappedLabel, parse_excel
from .field_index import FieldIndex, FieldHit, normalize_label

__all__ = [
    "parse_excel",
    "TextParseResult",
    "UnmappedLabel",
    "FieldIndex",
    "FieldHit",
    "normalize_label",
]
