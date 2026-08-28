---
layout: page
title: Resolver API
description: Local HTML and JSON endpoints for DOI discovery, semantic selection, and verified realization delivery.
permalink: /resolver-api/
---

Start the local service:

```sh
vao serve --host 127.0.0.1 --port 8765
```

The bundled server accepts only loopback addresses. It is a local inspection and
integration interface, not a public edge service.

## Endpoints

| Method and path | Result |
| --- | --- |
| `GET /` | DOI entry form. |
| `GET /health` | JSON liveness response. |
| `GET /resolve?doi={doi}` | Human-readable record and VAO overview. |
| `GET /api/community` | Locally cataloged community records and synchronization state. |
| `GET /api/resolve/{doi}` | JSON record, manifest summary, assets, groups, community status, conformance, and warnings. |
| `GET /api/resolve/{doi}/realization` | Verified realization bytes or a delivery plan. |

Percent-encode a DOI when it is part of the path. On a VAO 0.5 record, the resolver uses
the bootstrap declared by `vao-release.json`; a `carrier-member` distribution can then
lead to the preservation carrier. The optional `file` query parameter overrides the
start carrier with an exact `.vao` file key.

## Realization selection query

The realization endpoint accepts one direct identity or semantic constraints:

| Parameter | Meaning |
| --- | --- |
| `identifier` | Realization, logical-asset, or asset-group ID. |
| `asset` | Logical-asset ID. |
| `group` | Asset-group ID. |
| `kind` | `audio`, `video`, `geometry`, `image`, `document`, `data`, `event`, `software`, or `other`. |
| `quality` | VAO tier or `preview`/`low`/`medium`/`high`/`full` alias. |
| `media_type` | Exact media type or wildcard such as `audio/*`. |
| `max_bytes` | Integer bytes or a suffix such as `100MiB`. |
| `capability` | Required capability IRI/ID declared by a group. |
| `profile` | Materialized profile IRI/ID declared by a group. |
| `prefer` | `best` (default) or `smallest`. |
| `chunks` | Inline verified chunk `INDEX` or `START:STOP`, with an exclusive stop. |
| `plan` | `1`, `true`, or `yes` returns JSON without materializing bytes. |

Examples:

```text
GET /api/resolve/10.5281%2Fzenodo.12345678/realization?identifier=urn%3Aexample%3Ar&plan=1
GET /api/resolve/10.5281%2Fzenodo.12345678/realization?kind=geometry&quality=low&max_bytes=100MiB
GET /api/resolve/10.5281%2Fzenodo.12345678/realization?identifier=urn%3Aexample%3Aaudio&chunks=4:8
```

## Delivery behavior

The byte-response path first materializes into a temporary file and verifies the result.
Only then does it return the payload. Response headers include:

- `Content-Type`, derived from the realization media type;
- `Content-Length`;
- `Content-Disposition` with a safe basename;
- `X-VAO-Resolved-DOI`;
- an SHA-256 `ETag` when an output digest is available;
- `X-Content-Type-Options: nosniff`.

The plan path returns the trusted Zenodo content URL, delivery kind, member, compression,
extent, and ranges. A stored member may be fetched directly by a capable client, but that
client assumes responsibility for final VAO digest verification.

## Caching and deployment

Discovery responses have a bounded in-memory service cache. Zenodo JSON and small byte
ranges also use the configured persistent SQLite cache. `--cache-ttl` controls the
service-level cache lifetime.

A public resolver should be implemented as a separately reviewed deployment using the
library layer, with TLS, authentication or access policy, request and bandwidth limits,
observability, abuse controls, and an explicit cross-origin policy. The bundled server
must not be used as that deployment boundary.
