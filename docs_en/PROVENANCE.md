# Provenance and Showcase Scope

[中文](../docs/PROVENANCE.md) | **English** | [Overview](../README_en.md)

The code is selected from the custom MQElite project, providing a core subset that can be read independently and run offline.

## Dependency Scope and Adaptations

Domain, storage, archive bundle/eligibility, index/database/tokenizer, and recall form a runnable subset depending only on the standard library. The index package exports omit the production source loader that was not selected, avoiding imports of complete lifecycle modules.

The polling source retains its original algorithm; its transport dependency retains only two original exception definitions. Real socket/process transport implementations were not copied. That module is explicitly marked as a type excerpt, not a client capable of connecting to Codex.

Selected unit tests from the original project cover the retained mechanisms. Chinese persona names in three test files were consistently replaced with synthetic labels, while preserving the relevant tokenization-boundary assertions. The test material itself is synthetic and contains no real conversation records.

The date-reading showcase tests, demonstration, check script, documentation, and repository scaffolding were newly written for this showcase and are not presented as unchanged production artifacts. The selected source tests are also not the private project's complete acceptance suite.

## Excluded Material

The repository contains no live conversation data, authentication material, production configuration, real persona identifiers, sockets, host paths, old archives, Git history, or installation artifacts. Private pipelines, the authoritative Router history adapter, source loaders, the complete archive lifecycle and cleanup, the multi-persona runner, real transport, and MCP/CLI interfaces are outside the code excerpts.

The architecture and mechanism pages explain surrounding behavior based on source code and its owning documentation. Those explanations are not executable substitutes for omitted components.

## Upstream Attribution

MQElite's original NOTICE describes it as an independent implementation referencing the public shape and behavior of MemoQuasarEterna L2, and states that no legacy runtime code or production data is included. Codex API names are used for interoperability; no OpenAI runtime implementation is copied here.

The selected implementation depends on the Python standard library and SQLite FTS5. Capabilities provided by these upstream components are not counted as custom MQElite contributions.

The original project's LICENSE and NOTICE texts are preserved in [SOURCE_NOTICES.md](SOURCE_NOTICES.md), with the inherited scope distinguished from newly written materials.
