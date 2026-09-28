"""Which Schoology addresses a parent may be sent to."""

import pytest

from app import config, schoology


@pytest.mark.parametrize("domain, allowed", [
    ("irvingisd.schoology.com", True),
    ("someother.schoology.com", True),  # matches *.schoology.com
    ("IrvingISD.Schoology.com ", True),
    ("evil.example.com", False),
    ("schoology.com.evil.example", False),
    ("irvingisd.schoology.com/login", False),
    ("", False),
])
def test_default_rules(domain, allowed):
    assert schoology.is_allowed_domain(domain) is allowed


def test_a_listed_district_with_its_own_address_is_allowed(monkeypatch):
    monkeypatch.setattr(config, "SCHOOLOGY_DISTRICTS", [{"name": "Coppell ISD", "domain": "lms.coppellisd.com"}])
    assert schoology.is_allowed_domain("lms.coppellisd.com")
    assert not schoology.is_allowed_domain("other.coppellisd.com")
