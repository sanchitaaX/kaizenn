from shop.db import connect, find_customer


def test_finds_alice():
    assert find_customer(connect(), "alice") == [(1, "alice", "alice@example.com", "gold")]


def test_finds_bob():
    assert find_customer(connect(), "bob") == [(2, "bob", "bob@example.com", "standard")]


def test_unknown_customer_returns_nothing():
    assert find_customer(connect(), "zed") == []


def test_lookup_is_exact_match():
    assert find_customer(connect(), "ali") == []
