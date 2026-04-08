"""Offline checks for segmented birthday control classification.

Run:
    uv run python test/test_birthday_segment_detection.py
"""
from __future__ import annotations

from src.browser.helpers import _birthday_segment_field, _birthday_segment_order


def _test_labelled_spinbutton_order():
    infos = [
        {"label": "month, ", "max": 12, "text": "04"},
        {"label": "day, ", "max": 31, "text": "08"},
        {"label": "year, ", "max": 9999, "text": "2026"},
    ]
    assert _birthday_segment_order(infos) == ["month", "day", "year"]


def _test_placeholder_segment_order():
    infos = [
        {"label": "", "max": 0, "text": "mm", "placeholder": ""},
        {"label": "", "max": 0, "text": "dd", "placeholder": ""},
        {"label": "", "max": 0, "text": "yyyy", "placeholder": ""},
    ]
    assert _birthday_segment_field(infos[0], 0) == "month"
    assert _birthday_segment_field(infos[1], 1) == "day"
    assert _birthday_segment_field(infos[2], 2) == "year"
    assert _birthday_segment_order(infos) == ["month", "day", "year"]


def _test_unknown_segments_fall_back_to_default_order():
    infos = [
        {"label": "", "max": 0, "text": "04", "placeholder": ""},
        {"label": "", "max": 0, "text": "08", "placeholder": ""},
        {"label": "", "max": 0, "text": "2026", "placeholder": ""},
    ]
    assert _birthday_segment_order(infos) == ["month", "day", "year"]


def _main():
    _test_labelled_spinbutton_order()
    _test_placeholder_segment_order()
    _test_unknown_segments_fall_back_to_default_order()
    print("Birthday segment detection tests passed")


if __name__ == "__main__":
    _main()
