---
layout: page
title: Security and integrity
description: Trust boundaries, archive defenses, repository restrictions, and integrity guarantees of the VAO resolver.
permalink: /security/
---

## Trust model

The resolver separates discovery, transport, and content identity:

1. A DOI selects a Zenodo record.
2. The record supplies files and repository relations.
3. The VAO manifest supplies semantic capabilities and exact realization identity.
4. The carrier descriptor supplies embedded paths and pins the exact manifest bytes.
5. The requested bytes are untrusted until their declared extent and digest pass.

The moderated VAO community is authoritative for curated discovery only. It does not
replace exact DOI, release, realization, or checksum identity.

## Network boundary

- Production and Sandbox use separate HTTPS host allowlists.
- Redirect targets are checked against the resolved instance.
- URL credentials are rejected.
- Repository distributions must use the VAO Zenodo repository type, supported Records
  API profile, exact instance, and `version-pid-record-file` resolution policy.
- Distribution URLs are derived from record-owned files; manifests cannot turn the
  resolver into an arbitrary HTTP proxy.
- Retry behavior is bounded and limited to transient status codes and transport errors.

The CLI accepts no Zenodo token and implements no mutation endpoint.

## Reference-validator boundary

Full conformance executes `Tools/vao04.py` from the configured VAO Standard checkout.
That checkout is trusted executable code, not untrusted VAO input. Operators should use
the signed `v0.4.0` release from `modavis-project/vao-standard`; CI verifies its signed
tag before running the release gate. `VAO_STANDARD_ROOT` must not identify an untrusted
or writable third-party directory.

## Archive boundary

The ZIP reader supports single-disk ZIP and ZIP64 and rejects:

- encrypted or multi-disk archives;
- unsafe, absolute, traversal, backslash, or NUL-containing paths;
- duplicate, Unicode-NFC-equivalent, and NFC/case-fold-equivalent paths;
- control characters, excessive path depth, and unknown carrier roots;
- symbolic links and unsupported special entries;
- unsupported compression methods;
- inconsistent central-directory/local-header metadata;
- out-of-bounds sizes, offsets, entry counts, compression ratios, total extents, or
  oversized metadata structures.

Stored and Deflate members are supported. Deflate requires the whole compressed member,
but not the rest of the carrier. Decompression is streamed and checked against declared
size, ZIP CRC-32, and VAO SHA-256.

## Integrity guarantees

| Operation | Guarantee |
| --- | --- |
| Remote inspection | Manifest/carrier structure and exact manifest binding; realization bytes remain unread. |
| Embedded fetch | Member extent, ZIP CRC-32, byte size, and realization SHA-256. |
| Repository fetch | Exact version/record/file identity, byte size, and realization SHA-256. |
| Pack-member fetch | Exact pack-manifest bytes, safe member binding, member extent, CRC, and realization SHA-256. |
| Chunk fetch | Complete inline chunk-table structure plus SHA-256/SHA-512 for every returned chunk. |
| Full download | Zenodo MD5 when supplied, release SHA-256 when inventoried, carrier checks, every embedded payload, and reference conformance by default. |

A selective pack-member fetch intentionally does not read the entire outer pack, so it
cannot claim the outer pack's full SHA-256. The result explicitly reports
`outerPack: not-fully-read`; the requested member itself is still verified.

A verified byte chunk is not automatically a playable audio interval or independently
valid mesh. Media-level usability requires a format-aware index or container contract.

## Local writes

- Outputs are first written to uniquely named temporary files or directories.
- Integrity checks complete before atomic replacement.
- Existing targets are refused.
- Asset-group acquisition stages the complete dependency closure before committing the
  destination directory.
- Metadata edits create a new release identity and leave the source carrier unchanged.
- Publication preparation is offline, transactional, and explicitly records unresolved
  repository identity and rights decisions.

## Service boundary

The bundled HTTP service refuses non-loopback addresses. A public resolver requires a
separately reviewed service architecture with TLS, authentication or access policy,
request/concurrency/egress/storage quotas, observability, abuse controls, and an
explicit cross-origin policy. The Zenodo allowlist, redirect checks, conformance gate,
and final digest verification remain mandatory in such a deployment.
