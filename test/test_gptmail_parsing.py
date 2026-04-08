"""Offline checks for GPTMail response-shape normalization.

Run:
    uv run python test/test_gptmail_parsing.py
"""
from __future__ import annotations

from src.mail.gptmail import _coerce_records, _combined_mail_text, _extract_code


def _test_coerce_records_from_nested_dict():
    payload = {
        "success": True,
        "data": {
            "emails": [
                {"id": 1, "subject": "Your ChatGPT code is 123456"},
            ]
        },
    }
    records = _coerce_records(payload)
    assert len(records) == 1, records
    assert records[0]["id"] == 1, records


def _test_coerce_records_from_list_payload():
    payload = [
        {"id": "abc", "content": "Use code 654321"},
        "ignored",
    ]
    records = _coerce_records(payload)
    assert len(records) == 1, records
    assert records[0]["id"] == "abc", records


def _test_coerce_records_from_single_detail_record():
    payload = {
        "id": "mail-1",
        "subject": "Verification",
        "html_content": "<b>112233</b>",
    }
    records = _coerce_records(payload)
    assert len(records) == 1, records
    assert records[0]["id"] == "mail-1", records


def _test_extract_code_from_combined_text():
    mail = {
        "subject": "Your ChatGPT code",
        "text_content": "Please use 445566 to continue",
    }
    combined = _combined_mail_text(mail)
    code = _extract_code(combined)
    assert code == "445566", code


def _main():
    _test_coerce_records_from_nested_dict()
    _test_coerce_records_from_list_payload()
    _test_coerce_records_from_single_detail_record()
    _test_extract_code_from_combined_text()
    print("GPTMail parsing tests passed")


if __name__ == "__main__":
    _main()
