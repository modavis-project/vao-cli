# VAO CLI

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.22133810.svg)](https://doi.org/10.5281/zenodo.22133810)
[![VAO 0.5.0 candidate](https://img.shields.io/badge/VAO-0.5.0%20candidate-2C5F73.svg)](https://github.com/modavis-project/vao-standard)

VAO CLI is a reference command-line client for the
[Virtual Acoustic Object (VAO) Standard](https://github.com/modavis-project/vao-standard).
It resolves DOI-identified VAOs, inspects their semantic inventory, selects exact
representations, retrieves only the bytes required for a realization, and materializes
custom local carriers from a published release.

The client treats repository metadata, manifests, carriers, and payloads as distinct
trust layers. Full conformance is delegated to the validator matching the manifest
version. VAO 0.4.0 remains supported; VAO 0.5.0 adds the two-carrier Zenodo profile and
exact `carrier-member` retrieval required by large, dynamic datasets.

> The Zenodo integration is read-only. VAO CLI cannot create, edit, publish, submit, or
> delete Zenodo records. Its publication command only prepares a blocked local review
> package.

## Current release

| Component | Version |
| --- | --- |
| VAO CLI | 0.3.0 |
| Supported VAO versions | 0.4.0 final; 0.5.0 candidate |
| Python | 3.11 or newer |
| Release DOI | [10.5281/zenodo.22133810](https://doi.org/10.5281/zenodo.22133810) |

Legacy VAO 0.3.3 manifests may be inspected with explicitly limited structural checks.
No 0.3 draft is presented as a current standard or as later-version conformance.

## Capabilities

- Resolve Zenodo version and concept DOIs while reporting the exact resolved version.
- Inspect standalone `vao-manifest.json` files and remote `.vao` ZIP/ZIP64 carriers
  through bounded HTTP ranges.
- List logical assets, realizations, modalities, quality tiers, profiles, asset groups,
  carrier population, relations, and version history.
- Select realizations by identity, asset, group, modality, quality, media type, maximum
  extent, capability, or materialized profile.
- Acquire embedded members, exact Zenodo repository distributions, VAO pack members,
  members of another release-declared carrier, dependency-closed asset groups, and
  verified inline chunks.
- Materialize immutable custom carriers from selected realizations or group closures
  while retaining the release's exact manifest bytes.
- Download and verify complete carriers; validate, extract, compare, and revise local
  VAO releases without overwriting existing outputs. Metadata revision remains a
  VAO 0.4-only operation in this release.
- Maintain a local SQLite cache and catalog for the moderated
  [`virtual-acoustic-objects`](https://zenodo.org/communities/virtual-acoustic-objects/)
  community.
- Serve the same resolver through a loopback-only HTML and JSON interface.

The moderated community supports discovery. It does not replace a version DOI, VAO
release identifier, realization identifier, byte extent, or digest.

## Choose a command

| Task | Command |
| --- | --- |
| Check the installation and standard checkout | `vao doctor` |
| Validate a local `.vao` carrier | `vao validate` |
| Resolve an exact DOI | `vao resolve --exact` |
| Inspect assets and realizations | `vao inspect` |
| Find a suitable realization | `vao select` |
| Retrieve one realization | `vao fetch` |
| Build a custom carrier | `vao materialize` |
| Download the bootstrap or preservation carrier | `vao download` |
| Start the local resolver interface | `vao serve` |

Run `vao COMMAND --help` for the complete options of a command.

## Installation

VAO CLI requires both the Python package and a trusted checkout of the released VAO
Standard. Install the release wheel in a virtual environment and obtain the standard:

```sh
python3 -m venv .venv
. .venv/bin/activate
git clone https://github.com/modavis-project/vao-cli.git
python -m pip install ./vao-cli

git clone https://github.com/modavis-project/vao-standard.git
export VAO_STANDARD_ROOT="$(cd vao-standard && pwd)"
vao doctor
```

To work from source, clone this repository and run `python -m pip install -e .` from its
root. For tests and development tools, use `python -m pip install -e '.[dev]'`.

The standard checkout supplies the normative schemas and reference validator; the CLI
package supplies their Python runtime dependencies. Treat that checkout as executable
code. Use the signed `v0.4.0` tag for final VAO 0.4 validation; pin and review the
standard commit used for a VAO 0.5 release candidate.

## First validation

The standard repository includes a conforming synthetic carrier:

```sh
vao validate "$VAO_STANDARD_ROOT/Fixtures/VAO04/carriers/minimal.vao"
```

`vao validate` runs bounded carrier and payload checks followed by the authoritative
version-matched reference validator. `--structural-only` performs the bounded local
checks but makes no conformance claim.

## DOI discovery and acquisition

Set `VAO_DOI` to an exact Zenodo version DOI for a published VAO:

```sh
export VAO_DOI=10.5281/zenodo.12345678

vao resolve "$VAO_DOI" --exact
vao inspect "$VAO_DOI" --exact --assets --groups --archive
vao select "$VAO_DOI" --kind geometry --quality low --max-bytes 100MiB

vao fetch "$VAO_DOI" urn:example:realization:model:mobile \
  --output model.glb --dry-run
vao fetch "$VAO_DOI" urn:example:realization:model:mobile \
  --output model.glb

vao materialize "$VAO_DOI" --kind audio --quality mobile \
  --output mobile-audio.vao
vao download "$VAO_DOI" --output-dir Downloads
vao download "$VAO_DOI" --complete --output-dir Preservation
```

Concept DOIs resolve to the current version and both identities are reported. Use
`--exact` whenever moving resolution would undermine reproducibility. Global output
options precede the command:

```sh
vao --json inspect "$VAO_DOI" --assets
vao --quiet download "$VAO_DOI" --output-dir Downloads
vao --no-color doctor
```

## Selective delivery guarantees

| Delivery path | Verified result |
| --- | --- |
| Embedded stored/Deflate member | ZIP bounds, CRC-32, realization byte size, and SHA-256 |
| Exact repository distribution | Version record, record/file binding, byte size, and SHA-256 |
| VAO `pack-member` distribution | Exact pack-manifest binding, safe member path, member extent, CRC-32, and SHA-256 |
| VAO `carrier-member` distribution | Exact version record, release inventory, carrier ID, manifest and descriptor binding, member extent, CRC-32, and realization SHA-256 |
| Inline chunk range | Consecutive table structure and each selected SHA-256/SHA-512 digest |
| Complete carrier | Repository checksum when supplied, carrier structure, embedded payloads, and version-matched reference conformance |

A verified chunk is an exact byte extent, not necessarily a playable audio interval or
self-contained geometry fragment. A selectively acquired pack member proves its own
identity; it does not prove the digest of an unread outer pack or carrier. The client
reports that boundary explicitly. A full `vao download` verifies the outer carrier hash.

## Local operations

```sh
vao extract object.vao urn:example:realization:audio --output audio.wav
vao compare object-v1.vao object-v2.vao

vao metadata show object.vao --output metadata.json
vao metadata apply object.vao metadata.json --output object-v2.vao

vao publication prepare object-bootstrap.vao object-preservation.vao \
  --readme README.pdf --output publication-review --copy-carrier
```

Metadata application leaves the input untouched, creates a new release identifier,
increments the revision, records the superseded release, updates the exact carrier
binding, and requires the resulting carrier to pass the 0.4.0 reference validator.
For VAO 0.5, use `materialize`; the metadata revision command does not rewrite a
published 0.5 manifest.

Publication preparation creates checksums and schema-valid draft descriptors in a
local directory. Pending repository identifiers, current Zenodo metadata review,
rights, consent, privacy, and access decisions keep the readiness state false. Nothing
is sent to Zenodo.

## Resolver service

```sh
vao serve --host 127.0.0.1 --port 8765
```

The bundled service is deliberately restricted to loopback addresses. It is a local
inspection and integration interface, not a production public service. See the
[resolver API](docs/resolver-api.md) and [security model](docs/security.md).

## Documentation

- [Getting started](docs/getting-started.md)
- [Command reference](docs/command-reference.md)
- [Architecture and conformance boundary](docs/ARCHITECTURE.md)
- [Compatibility and limitations](docs/compatibility.md)
- [Resolver API](docs/resolver-api.md)
- [Security and integrity](docs/security.md)
- [Publication preparation](docs/publication-preparation.md)

The complete documentation is prepared for GitHub Pages at
[modavis-project.github.io/vao-cli](https://modavis-project.github.io/vao-cli/).

## Related projects

- [VAO Standard](https://github.com/modavis-project/vao-standard) — normative schemas,
  documentation, fixtures, and versioned reference validators
- [VAO Standard documentation](https://modavis-project.github.io/vao-standard/)
- [MODAVIS Ontology Network](https://github.com/modavis-project/modavis-ontology-network)
  — the published semantic vocabulary network used by VAO metadata
- [MODAVIS Ontology Network documentation](https://modavis-project.github.io/modavis-ontology-network/)
- [Virtual Acoustic Objects community](https://zenodo.org/communities/virtual-acoustic-objects/)
  — moderated discovery of published VAO records

## Development and citation

```sh
python -m pip install -e '.[dev]'
export VAO_STANDARD_ROOT=/path/to/vao-standard
python tools/check_release.py
```

Project policy and citation files are
[CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md),
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), and [CITATION.cff](CITATION.cff).

VAO CLI is maintained by **Dominik Ukolov**, Digital Humanities (Image/Object),
Friedrich Schiller University Jena; also Research Group DIGITAL ORGANOLOGY, Leipzig
University. Affiliations identify the developer and do not imply institutional
endorsement.

This work was developed as part of the **MODAVIS** doctoral research project
(2022–2026). Dominik Ukolov's doctoral research was supported by the German
Academic Scholarship Foundation (*Studienstiftung des deutschen Volkes*).
Funding and affiliations do not imply endorsement of the project's technical
or scientific claims.

The software and documentation in this repository are licensed under
[Apache License 2.0](LICENSE). The VAO standard is a separate, cited work with its own
license terms.
