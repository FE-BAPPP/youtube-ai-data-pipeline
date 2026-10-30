"""Tests cho helper trích xuất JSON từ response của AI."""

import json

import pytest

from core.ai_analyzer import _extract_json


def test_plain_json_object():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_plain_json_array():
    assert _extract_json('[{"id": 0}, {"id": 1}]') == [{"id": 0}, {"id": 1}]


def test_json_code_fence():
    raw = '```json\n{"a": 1}\n```'
    assert _extract_json(raw) == {"a": 1}


def test_bare_code_fence():
    raw = '```\n[1, 2, 3]\n```'
    assert _extract_json(raw) == [1, 2, 3]


def test_code_fence_with_surrounding_text():
    raw = 'Here is the result:\n```json\n{"a": 1}\n```\nHope this helps!'
    assert _extract_json(raw) == {"a": 1}


def test_multiline_json_in_fence():
    raw = '```json\n{\n  "a": 1,\n  "b": ["x", "y"]\n}\n```'
    assert _extract_json(raw) == {"a": 1, "b": ["x", "y"]}


def test_invalid_json_raises():
    with pytest.raises(json.JSONDecodeError):
        _extract_json("this is not json at all")
