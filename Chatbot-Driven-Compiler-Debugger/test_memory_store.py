"""Unit tests for memory_store.py."""
import os
import tempfile
import pytest
from memory_store import (
    init_memory_db,
    record_experience,
    retrieve_similar_experiences,
    normalize_signature,
)


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    init_memory_db(db_path)
    yield db_path
    if os.path.exists(db_path):
        os.unlink(db_path)


def test_record_and_retrieve_experience(temp_db):
    flaws = ["uninitialized_var", "unbuffered_io"]
    rec_id = record_experience(
        language="cpp",
        flaws=flaws,
        flaw_summary="Fixed uninitialized stack variable and enabled fast IO",
        original_code="int main() { int x; x++; }",
        patch_code="int main() { int x = 0; x++; }",
        success=True,
        runtime_gain_pct=15.5,
        db_path=temp_db,
    )
    assert rec_id > 0

    retrieved = retrieve_similar_experiences("cpp", flaws, limit=3, db_path=temp_db)
    assert len(retrieved) == 1
    assert retrieved[0]["flaw_signature"] == normalize_signature(flaws)
    assert retrieved[0]["runtime_gain_pct"] == 15.5
    assert retrieved[0]["success"] == 1


def test_partial_match_retrieval(temp_db):
    record_experience(
        language="cpp",
        flaws=["division_by_zero", "unbuffered_io"],
        flaw_summary="Fixed division by zero",
        original_code="int y = 5 / 0;",
        patch_code="if (d != 0) int y = 5 / d;",
        success=True,
        db_path=temp_db,
    )

    # Query with slightly different flaw list that shares division_by_zero
    retrieved = retrieve_similar_experiences("cpp", ["division_by_zero"], limit=3, db_path=temp_db)
    assert len(retrieved) >= 1
    assert "division_by_zero" in retrieved[0]["flaw_signature"]

