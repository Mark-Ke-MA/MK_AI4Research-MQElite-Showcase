# Core Mechanisms and Tradeoffs

[中文](../docs/MECHANISMS.md) | **English** | [Overview](../README_en.md)

## 1. Determine Dates Before Downloading Bodies

[DurablePollingSource](../src/mqelite/codex/source.py) first calls `thread/turns/list` with `itemsView="notLoaded"`, retaining turn identity, lifecycle status, and timestamps. It then follows opaque cursors returned by the server to read bodies of turns whose start or completion times may fall within the requested interval. The metadata inventory is cached inside the reader.

When a body batch exceeds the transport size limit, the connection is reset and the batch size halved; if a single turn still exceeds the limit, an error is raised. If relevant metadata changes while bodies are being read, the cache is discarded and the requested window is fully refreshed once; another change is reported to the caller. A cache containing active turns is invalidated before reuse.

The tradeoff is extra metadata and positioning requests in exchange for fewer body downloads, not a guarantee of constant reading cost: metadata still covers history, relevant bodies may be large, and source changes may trigger a refresh. Public tests use in-memory protocol fixtures and cannot establish compatibility with every Codex version.

## 2. Recover After a File Commit Succeeds but the Ledger Update Fails

[DailyReconciler](../src/mqelite/storage/reconcile.py) computes canonical bytes and their digest, holds a persona's daily lock, commits changed JSON through staging, validation, and atomic replacement, then updates the ingestion ledger.

If the second step fails, a retry finds that the authoritative file already matches, preserves that file, and repairs the state. The selected regression tests inject this failure; a second normal execution with identical input does not rewrite the document. This is not a single transaction spanning the filesystem and SQLite, but an explicit recovery order for partial success.

[atomic.py](../src/mqelite/storage/atomic.py) shows staging and fsync ordering; [layout.py](../src/mqelite/storage/layout.py) shows explicit paths, permission modes, and symlink checks on managed paths.

## 3. Preserve Verifiable Archive Evidence

[build_archive](../src/mqelite/archive/bundle.py) writes the original daily bytes into a tar/gzip bundle with deterministic member metadata and a manifest. The external manifest binds the compressed bundle bytes; member hashes bind the uncompressed daily files. Validation checks identity, dates, paths, sizes, hashes, and format constraints. The legacy-format reader permits unknown fields while preserving original bytes.

Reproducibility requires exactly identical inputs, including caller-supplied creation time and tool version. Hashes establish integrity relative to a trusted manifest, not digital-signature authentication.

[archive_ready](../src/mqelite/archive/eligibility.py) adds completeness, conflict, index, and runtime-state conditions beyond the calendar cutoff. Building a bundle does not mean hot files can be deleted. The retained `commit_archive` primitive writes generations and the active pointer; higher-level planning, index coordination, coverage verification, and hot-file cleanup have not been copied into this repository.

## 4. Keep Recall Inexpensive, Bounded, and Explainable

[Tokenization](../src/mqelite/index/tokenize.py) applies Unicode NFKC and case folding, extracting Latin terms and overlapping CJK bigrams and trigrams; single-character segments retain one character. SQLite FTS5 first retrieves a capped candidate set. The [retrieval logic](../src/mqelite/index/database.py) then scores term overlap, adds a normalized-substring-match bonus, and can optionally apply a recency weight of up to 1.1×.

Results are bounded by count and content character length and can be filtered by role and date. A character budget is not a model token budget. Retrieval stops at the first result that exceeds the budget, so an empty list may be returned even if shorter results occur later. The candidate cap and lexical matching also mean this is not exhaustive semantic retrieval.

Recall opens a ready index read-only; it does not create a missing index or repair an unready one at query time. Selected tests compare database bytes and modification times before and after queries and verify that missing or unready states do not trigger repair writes.

## Evaluating the Optimization

Date-first reading lowers reading cost by reducing transfers of irrelevant bodies. Evaluating this optimization requires comparing both whether canonical output stays the same and whether the reading scope shrinks, rather than merely observing faster requests. The [synthetic tests](VALIDATION.md) here verify body filtering, caching, and exception handling; they do not provide a real online performance benchmark.
