---
layout: page
title: Publication preparation
description: The local, non-publishing workflow for reviewing a VAO 0.4.0 Zenodo record package.
permalink: /publication-preparation/
---

`vao publication prepare` assembles a local review package. It does not authenticate to
Zenodo and cannot create, upload, modify, submit, publish, or delete a record.

```sh
vao publication prepare object.vao \
  --output publication-review --copy-carrier
```

The command requires the input carrier to pass both bounded local checks and the
released VAO 0.4.0 reference validator. It then creates:

```text
publication-review/
├── object.vao                       optional copied carrier
├── vao-manifest.json                exact carrier manifest bytes
├── vao-release.template.json        schema-valid repository descriptor template
├── zenodo-metadata.template.json    schema-valid legacy projection for review
├── SHA256SUMS
└── publication-readiness.json
```

The release and metadata templates deliberately contain pending repository identity and
restricted-access review values. Their schemas are validated, but the metadata template
targets the legacy Zenodo Depositions compatibility profile defined by VAO 0.4.0. It is
not asserted to be a complete request for Zenodo's current InvenioRDM Records API or a
substitute for reviewing the web form.

## Recommended record shape

A single-carrier record normally exposes:

```text
object.vao
vao-manifest.json
vao-release.json
README.md                     optional human documentation
```

The standalone manifest supports inexpensive capability discovery and is an exact byte
copy of the carrier manifest. `vao-release.json` binds the exact version record and file
inventory after the repository identifiers exist. Concept DOIs support discovery;
acquisition bindings use exact version, record, file, byte-size, and digest identity.

Modular records use the VAO 0.4.0 repository and `pack-member` distribution model rather
than a second resolver manifest. Every family member retains exact release, record,
file, member, extent, and digest bindings. A small complete bootstrap group remains
valuable for resilient inspection and use.

## Readiness boundary

The generated `readyForLivePublication` value remains false. A live record still
requires review of:

- creators, contributors, ORCID/ROR identifiers, funding, and subjects;
- rights, licenses, consent, privacy, access, and culturally sensitive material;
- exact Zenodo version and concept identifiers and the final file inventory;
- the current Zenodo metadata model and rendered record preview;
- remote resolution, conformance inspection, selective acquisition, full download, and
  moderated-community submission.

A repository metadata correction must not silently rewrite an existing VAO semantic
manifest. A semantic change creates a new VAO release and normally a new repository
version.
