"""JCS canonicalization vectors (LLD §A11.2)."""

from __future__ import annotations

import pytest

from fact import ProtocolError, canonicalize, nfc_normalize
from fact.errors import ErrorCode


@pytest.mark.parametrize("value,expected", [
    ({"b": 2, "a": 1}, b'{"a":1,"b":2}'),   # key ordering
    ({"x": 1.0}, b'{"x":1}'),               # 1.0 -> 1
    ({"x": -0.0}, b'{"x":0}'),              # -0 -> 0
    ({"k": None}, b'{"k":null}'),           # null preserved
    ({}, b"{}"),                            # empty object
    ([], b"[]"),                            # empty array
    ({"x": 1.5}, b'{"x":1.5}'),             # non-integer
    ([3, 1, 2], b"[3,1,2]"),                # array order preserved
])
def test_canonical_vectors(value, expected):
    assert canonicalize(value) == expected


def test_nested_key_ordering():
    assert canonicalize({"z": {"b": 1, "a": 2}, "a": 1}) == b'{"a":1,"z":{"a":2,"b":1}}'


def test_nan_infinity_rejected():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ProtocolError) as ei:
            canonicalize({"x": bad})
        assert ei.value.code == ErrorCode.MALFORMED


def test_unicode_is_not_normalized_by_canonicalizer():
    # Precomposed vs decomposed produce different bytes unless NFC applied first.
    precomposed = "café"        # café
    decomposed = "café"        # cafe + combining acute
    assert canonicalize(precomposed) != canonicalize(decomposed)
    assert nfc_normalize(decomposed) == precomposed
    assert canonicalize(nfc_normalize(decomposed)) == canonicalize(precomposed)
