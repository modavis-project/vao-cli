---
layout: home
title: VAO CLI
description: Reference resolution, validation, and selective acquisition for Virtual Acoustic Objects.
permalink: /
---

VAO CLI is the reference command-line client for the
[VAO Standard](https://github.com/modavis-project/vao-standard). It supports final VAO
0.4.0 and the VAO 0.5.0 candidate, dispatches to the matching reference validator, and
retrieves exact realizations without requiring a complete carrier download. It can also
assemble a validated custom carrier from any selected subset.

The Zenodo adapter is read-only. It accepts no access token and has no record creation,
upload, publication, metadata mutation, community-submission, or deletion operation.

## Documentation

| Topic | Document |
| --- | --- |
| Installation and first use | [Getting started](getting-started.md) |
| Commands and options | [Command reference](command-reference.md) |
| Resolution and delivery model | [Architecture](ARCHITECTURE.md) |
| Supported standard and limits | [Compatibility](compatibility.md) |
| Local HTTP interface | [Resolver API](resolver-api.md) |
| Trust and integrity | [Security](security.md) |
| Offline record preparation | [Publication preparation](publication-preparation.md) |

## Minimal workflow

```sh
export VAO_STANDARD_ROOT=/path/to/vao-standard
export VAO_DOI=10.5281/zenodo.12345678

vao doctor
vao resolve "$VAO_DOI" --exact
vao inspect "$VAO_DOI" --exact --assets --groups
vao fetch "$VAO_DOI" urn:example:realization --output result.bin --dry-run
vao materialize "$VAO_DOI" --group urn:example:mobile-group --output mobile.vao
```

## Assurance boundary

DOI resolution identifies a repository record. The release descriptor identifies its
exact carriers. The VAO manifest identifies the semantic release and realizations. Each
carrier descriptor binds embedded paths to the exact manifest bytes. A completed
acquisition is not reported as verified until its declared extent and digest pass.

Conformance proves adherence to the selected VAO version's structural and semantic
rules. It does not
certify that a scientific measurement, interpretation, attribution, rights assertion,
or represented object is empirically true. Those claims remain dependent on evidence,
methods, and qualified review.
