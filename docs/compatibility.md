---
layout: page
title: Compatibility and limitations
description: Supported VAO release, conformance roles, tested delivery paths, and explicit implementation limits.
permalink: /compatibility/
---

## Supported standard

VAO CLI 0.2.0 targets the published
[VAO Standard 0.4.0](https://doi.org/10.5281/zenodo.22122774). Full conformance uses the
reference validator and schemas from the immutable `v0.4.0` source release. The release
compatibility tests validate and selectively materialize the standard's official
`Fixtures/VAO04/carriers/minimal.vao` carrier.

VAO 0.3.3 support is limited to explicitly requested structural inspection. Version
0.3.3 was an unpublished development boundary retained by the standard for migration
testing; the CLI does not describe it as a current public format.

## Implemented roles

| VAO role | Scope in VAO CLI 0.2.0 |
| --- | --- |
| Reader | Interprets a manifest only after full 0.4.0 validation unless the operator explicitly selects `--no-conformance`. |
| Validator | Combines bounded local carrier checks with the released authoritative reference validator. The CLI's local checks alone are not a VAO conformance claim. |
| Extractor | Validates carrier structure and exact embedded realization identity before atomic extraction. |
| Materializer | Supports embedded, exact Zenodo repository, and `pack-member` delivery with exact byte verification. |
| Repository projector | Produces schema-valid offline review templates; it performs no live repository mutation. |
| Writer | Descriptive metadata revision only; output receives a new release identity and must pass the reference validator. No general VAO authoring claim is made. |

The client does not claim linked-data projector, deterministic runtime, renderer, or
scientific-verification roles.

VAO 0.4.0's binding to the MODAVIS Ontology Network 0.1.0 is checked by the standard's
reference validator. VAO CLI preserves that declaration during its limited metadata
revision workflow, but does not perform RDF projection, SHACL validation, OWL reasoning,
or ontology-mediated scientific inference.

## Verified implementation matrix

- strict DOI normalization and exact/concept version handling;
- standalone-manifest and remote ZIP/ZIP64 discovery through bounded ranges;
- rejection of unsafe, control-character, Unicode-colliding, case-fold-colliding,
  encrypted, special-file, unsupported-compression, and resource-limit archive cases;
- semantic selection by identity, modality, quality, extent, media type, capability,
  profile, and asset-group dependency closure;
- verified stored/Deflate embedded acquisition, exact repository acquisition,
  `pack-member` acquisition, inline chunk acquisition, and transactional groups;
- complete download, local validation, safe extraction, release comparison, metadata
  revision, and offline publication preparation;
- production and Sandbox Zenodo host separation and redirect enforcement;
- Python 3.11 and 3.14 CI targets, isolated wheel installation, package metadata checks,
  documentation link checks, and a pinned GitHub Pages build workflow.

## Explicit limits

- External streaming-index realizations are exposed but not interpreted. VAO 0.4.0
  identifies the index realization but does not define one universal audio, geometry,
  or sensor index format.
- Verified chunks are exact byte extents; media decodability or independent usefulness
  requires a format-specific contract.
- Deflate acquisition requires the complete compressed member. Independently compressed
  media is most range-friendly when stored in the carrier.
- A selective pack-member operation verifies the member but cannot prove the unread
  outer pack's complete digest.
- The resolver follows only supported public Zenodo distributions. Arbitrary external
  manifest URLs are not acquisition sources.
- The local HTTP service is loopback-only and is not a production hosting stack.
- Repository metadata projection is a review aid. Current Zenodo metadata and the final
  rendered record require independent review before publication.
- Conformance does not establish scientific truth, evidentiary adequacy, attribution,
  consent, or rights validity.

## Version policy

CLI releases follow Semantic Versioning independently of the VAO format version. A
future CLI release may support more than one published VAO version, but every
conformance report identifies the exact standard version used. Compatibility-affecting
changes are recorded in [CHANGELOG.md](../CHANGELOG.md).
