"""Unit test for eval benchmark loader."""
import json
import os
import pytest


def test_samples_json_structure():
    eval_dir = os.path.dirname(__file__)
    sample_file = os.path.join(eval_dir, "samples_cpp.json")
    assert os.path.exists(sample_file)

    with open(sample_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert isinstance(data, list)
    assert len(data) >= 3
    for case in data:
        assert "id" in case
        assert "name" in case
        assert "code" in case
        assert "target_flaws" in case

