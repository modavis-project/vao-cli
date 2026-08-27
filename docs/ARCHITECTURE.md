---
layout: page
title: Architecture and feasibility
description: How VAO 0.4 semantics map DOI identity to verified Zenodo byte-range delivery.
permalink: /architecture/
---

## Resolution model

VAO 0.4.0 supplies the semantic and delivery layers used by the client:

```text
version DOI
  -> exact Zenodo record/file identity
  -> standalone vao-manifest.json or range-indexed .vao
  -> logical assets and asset groups (intent/capability)
  -> realizations (exact media type, byte size, SHA-256, quality tier)
  -> META-INF/vao-carrier.json (realization ID -> embedded payload path)
  -> Zenodo HTTPS byte ranges
```

The resolver therefore interprets the standard instead of inventing parallel
`type/lod/archive/member` semantics. `logicalAssets` describe the work; `realizations`
describe exact byte representations; `assetGroups` already express quality,
availability, selection policy, dependencies, total extent, required capabilities, and
profile materialization. `distributions` and repository bindings describe exact remote
availability. VAO 0.4 also defines `chunking` and optional streaming-index realization
links for verified range-addressable designs.

The manifest intentionally has no carrier paths. Embedded paths belong exclusively in
`META-INF/vao-carrier.json`, which pins the exact manifest bytes and release identity.
This separation is honored by the implementation.

## Feasibility matrix

| Operation | Feasible now? | Implementation / constraint |
| --- | --- | --- |
| DOI -> title, date, files, exact/concept identity | Yes | Zenodo Records API; concept resolution is reported explicitly. |
| DOI -> VAO modalities/capabilities | Yes | Read standalone manifest or remotely index the `.vao` ZIP/ZIP64. |
| Inspect a large `.vao` without full download | Yes | EOCD, ZIP64 records, central directory, local headers, manifest and carrier use bounded HTTP ranges. |
| Retrieve one embedded low-resolution model/audio realization | Yes | Resolve VAO ID to carrier path; range-read stored/deflated member; verify CRC, size, and SHA-256. |
| Direct browser-to-Zenodo range delivery | Repository-policy dependent | The CLI uses verified server-side range requests. Browser delivery additionally depends on the repository's current cross-origin response policy and is not a CLI guarantee. |
| Verified quantitative byte extent | Yes, when declared inline | Stored/repository realizations with a complete VAO 0.4 chunk table can be fetched by chunk index/range and checked independently. |
| External streaming-index interpretation | Not yet | The linked realization is visible, but its format requires a separately specified/index-aware adapter. |
| Arbitrary playable audio time slice / usable mesh subset | Not generically | Verified bytes are not automatically a self-contained media unit; usability remains format/index dependent. |
| Read one Deflate member without its compressed bytes | No | Deflate is sequential; the whole compressed member is required. `ZIP_STORED` is preferred for independently compressed media. |
| Infer “low resolution” from a filename | No and unnecessary | Use standard realization quality tiers and asset-group selection semantics. |
| Treat Zenodo version files as eternally immutable | Not absolutely | Use exact version DOI plus VAO SHA-256. Repository update policies can permit changes in limited circumstances. |
| Use the community as integrity authority | No | It is a moderated discovery authority; exact manifests and digests are integrity authority. |
| Resolve arbitrary external URLs from a manifest | Deliberately no | Only record-owned Zenodo endpoints are followed by this adapter. |
| Edit metadata in-place | No | VAO release identity covers semantics. The editor creates a new local release and leaves the source untouched. |
| Exact repository distribution | Yes | Requires the 0.4 Zenodo binding, exact version PID, record ID, file ID, public access, size, and final realization SHA-256. |
| Pack-member selective retrieval | Yes, with a bounded claim | Stored nested/external packs are indexed and the exact pack manifest/member identity is enforced. The output member is verified; the untouched outer pack is reported as not fully read. |

## Archive behavior

`ZIP_STORED` makes every payload a contiguous remote byte interval. Independently
compressed formats such as GLB, JPEG/WebP, FLAC, and COPC often benefit little from a
second ZIP compression layer. The metadata editor consequently writes stored entries.

Deflate is supported for compatibility, but only at whole-member granularity. The CLI
streams compressed input and decompressed output with bounded memory; it does not load a
large realization into RAM.

ZIP64 is required in practice for multi-gigabyte carriers. The reader supports one-disk
ZIP and ZIP64, validates record counts and bounds, caps central-directory size/entry
count, rejects encryption, unsafe paths, special files, unsupported compression, and
Unicode-normalized duplicates, and validates central/local header agreement.

## Trust boundaries

- DOI input is normalized and either resolved by the known Zenodo DOI fast path or an
  exact Zenodo DOI query.
- Production and Sandbox have distinct HTTPS host allowlists; redirects are checked.
- A VAO carrier path cannot redirect the client to an arbitrary origin.
- Remote discovery is structurally and semantically checked, but full payload integrity
  is established only when a realization or complete carrier is read.
- A selectively retrieved pack member can prove its own exact realization identity but
  cannot prove the SHA-256 of an unread outer pack. The CLI reports these two assurance
  levels separately.
- Zenodo's transport MD5 is checked on full downloads; VAO's SHA-256 remains the content
  identity.
- No upload token is accepted and no mutation endpoints exist.

## Service scaling path

The included service is a local integration interface: it caches discovery metadata and
uses Zenodo ranges while proxying verified realization bytes. A larger deployment can
add a content-addressed realization cache and, for stored entries, return a short-lived
delivery plan that lets a capable client range-fetch directly from Zenodo. The JSON API
can remain stable across that change because VAO IDs, not archive offsets, are the public
selection interface.

The bundled server refuses non-loopback bind addresses. A public service requires a
separate deployment design with authentication, quotas, monitoring, and an independently
reviewed network boundary.

Offsets are derived from the ZIP directory, not trusted as persistent VAO semantics.
The public identity remains realization ID, size, and SHA-256; pack acquisition also
requires the exact root-pinned pack manifest and safe member path.
