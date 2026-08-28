# Changelog

All notable changes to VAO CLI are recorded here. The project follows
[Semantic Versioning](https://semver.org/).

## 0.3.0 — 2026-08-28

- Adds VAO 0.5.0 candidate validation while retaining VAO 0.4.0 support.
- Resolves a release-declared bootstrap carrier by default and a preservation closure
  with `download --complete`.
- Retrieves `carrier-member` realizations from another carrier on the same exact
  Zenodo version record through verified HTTP ranges.
- Adds `materialize` for building validated custom carriers from explicit IDs,
  asset-group closures, or semantic filters without downloading an entire source
  carrier.
- Stages the VAO 0.5 single-record profile with one bootstrap carrier, one
  preservation-closure carrier, a standalone manifest, checksums, and an optional
  `README.pdf`.

## 0.2.0 — 2026-08-27

First public-release candidate.

- Implements DOI and Zenodo record resolution, exact version handling, and moderated-community discovery.
- Reads VAO 0.4.0 standalone manifests and range-indexed ZIP/ZIP64 carriers.
- Provides deterministic semantic selection and verified embedded, repository, pack-member, group, and chunk acquisition.
- Integrates the released VAO 0.4.0 reference validator and fails closed for operations that request conformance.
- Adds bounded structural validation, safe extraction, release comparison, metadata revision, and offline publication preparation.
- Adds the loopback-only resolver API, local cache and catalog, public documentation, packaging metadata, and release automation.
