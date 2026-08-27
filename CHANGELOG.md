# Changelog

All notable changes to VAO CLI are recorded here. The project follows
[Semantic Versioning](https://semver.org/).

## 0.2.0 — 2026-08-27

First public-release candidate.

- Implements DOI and Zenodo record resolution, exact version handling, and moderated-community discovery.
- Reads VAO 0.4.0 standalone manifests and range-indexed ZIP/ZIP64 carriers.
- Provides deterministic semantic selection and verified embedded, repository, pack-member, group, and chunk acquisition.
- Integrates the released VAO 0.4.0 reference validator and fails closed for operations that request conformance.
- Adds bounded structural validation, safe extraction, release comparison, metadata revision, and offline publication preparation.
- Adds the loopback-only resolver API, local cache and catalog, public documentation, packaging metadata, and release automation.
