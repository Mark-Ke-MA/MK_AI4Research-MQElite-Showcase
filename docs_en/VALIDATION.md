# Validation Record

[中文](../docs/VALIDATION.md) | **English** | [Overview](../README_en.md)

This page describes the behavior that the offline demonstration and synthetic tests can verify.

## Verification Commands

Requires Python 3.11+, POSIX file locking, SQLite FTS5, and Europe/London time-zone data:

```sh
python3 -B scripts/demo.py
python3 -B scripts/check.py
```

The demonstration and tests use only in-memory protocol fixtures and temporary files. They do not start an app-server, connect to local sockets, access production configuration or memory, or trigger model calls.

## Current Check Scope

| Mechanism | Evidence location | Purpose |
| --- | --- | --- |
| Memory days and DST | `tests/test_time_window.py` | Calendar boundaries, 23/25-hour days, and archive cutoff conditions |
| Native and legacy L2 formats | `tests/test_domain_l2.py` | Strict format, partial-coverage provenance, original bytes, and uncertain times |
| Daily file commits and recovery | `tests/test_storage_reconcile.py` | Idempotency, recovery after state failure, and path and staging-file handling |
| Archive bundles | `tests/test_archive.py` | Reproducible generation, byte preservation, and rejection of tampering and unsafe members |
| Retrieval and read-only behavior | `tests/test_index_recall.py` | Chinese/English lexical matching, budgets, time precision, and missing/unready index behavior |
| Date-based reading | `tests/test_date_first_showcase.py` | Body filtering, caching, batch halving on size limits, bounded retries after changes, and persona checks |

## Verified Scope

- `scripts/demo.py` verifies idempotent daily commits, Chinese recall, unchanged bytes after index reads, single-date archive validation, and a 23-hour spring clock-change day; temporary data is automatically cleaned up.
- `scripts/check.py` executes **51 tests, all passing, with 0 skipped**. Of these, 46 come from the selected original project tests and 5 are synthetic RPC date-reading tests.

## Validation Boundaries

These checks cannot establish current production scheduling health, completeness of real history, sufficient coverage of concurrent-process behavior, or compatibility with every Codex version. They do not include the complete production pipeline, complete monthly archive/index coordination, CLI/MCP, the cross-persona fleet, or deployment acceptance. The synthetic source uses a specified RPC contract; no live protocol probing was performed.
