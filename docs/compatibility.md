---
layout: page
title: Compatibility and limitations
description: Supported VAO release, conformance roles, tested delivery paths, and explicit implementation limits.
permalink: /compatibility/
---

## Supported standard

VAO CLI 0.3.0 supports the published
[VAO Standard 0.4.0](https://doi.org/10.5281/zenodo.22122774) and the 0.5.0 candidate in
the standard repository. Full conformance dispatches to `vao04.py` or `vao05.py` from
the selected checkout according to the document's own `formatVersion`. Compatibility
tests retain the immutable VAO 0.4 fixture and add VAO 0.5 two-carrier behavior.

VAO 0.3.3 support is limited to explicitly requested structural inspection. Version
0.3.3 was an unpublished development boundary retained by the standard for migration
testing; the CLI does not describe it as a current public format.

## Implemented roles

| VAO role | Scope in VAO CLI 0.3.0 |
| --- | --- |
| Reader | Interprets a manifest only after version-matched validation unless the operator explicitly selects `--no-conformance`. |
| Validator | Combines bounded local carrier checks with the released authoritative reference validator. The CLI's local checks alone are not a VAO conformance claim. |
| Extractor | Validates carrier structure and exact embedded realization identity before atomic extraction. |
| Materializer | Supports embedded, exact Zenodo repository, `pack-member`, and 0.5 `carrier-member` delivery, plus purpose-built local custom carriers with exact byte verification. |
| Repository projector | Produces schema-valid offline review templates for legacy 0.4 and the 0.5 two-carrier single-record profile; it performs no live repository mutation. |
| Writer | Descriptive metadata revision is limited to 0.4. Custom 0.5 materialization preserves the published semantic manifest and changes only carrier population. |

The client does not claim linked-data projector, deterministic runtime, renderer, or
scientific-verification roles.

The manifest's binding to the MODAVIS Ontology Network is checked by the applicable
standard validator. VAO CLI preserves that declaration during its limited metadata
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
  `pack-member` and `carrier-member` acquisition, inline chunk acquisition, and
  transactional groups;
- complete download, local validation, safe extraction, release comparison, metadata
  revision, custom carrier materialization, and offline publication preparation;
- production and Sandbox Zenodo host separation and redirect enforcement;
- Python 3.11 and 3.14 CI targets, isolated wheel installation, package metadata checks,
  documentation link checks, and a pinned GitHub Pages build workflow.

## Explicit limits

- External streaming-index realizations are exposed but not interpreted. VAO
  identifies the index realization but does not define one universal audio, geometry,
  or sensor index format.
- Verified chunks are exact byte extents; media decodability or independent usefulness
  requires a format-specific contract.
- Deflate acquisition requires the complete compressed member. Independently compressed
  media is most range-friendly when stored in the carrier.
- A selective pack/carrier-member operation verifies the requested member but cannot
  prove the unread outer container's complete digest.
- The resolver follows only supported public Zenodo distributions. Arbitrary external
  manifest URLs are not acquisition sources.
- The local HTTP service is loopback-only and is not a production hosting stack.
- Repository metadata projection is a review aid. Current Zenodo metadata and the final
  rendered record require independent review before publication.
- Conformance does not establish scientific truth, evidentiary adequacy, attribution,
  consent, or rights validity.

## Version policy

CLI releases follow Semantic Versioning independently of the VAO format version. A
CLI releases can support more than one VAO version, but every
conformance report identifies the exact standard version used. Compatibility-affecting
changes are recorded in [CHANGELOG.md](../CHANGELOG.md).
