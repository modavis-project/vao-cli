---
layout: page
title: Publication preparation
description: The local, non-publishing workflow for reviewing a VAO 0.5 two-carrier Zenodo record package.
permalink: /publication-preparation/
---

`vao publication prepare` assembles a local review package. It does not authenticate to
Zenodo and cannot create, upload, modify, submit, publish, or delete a record.

```sh
vao publication prepare object-bootstrap.vao object-preservation.vao \
  --readme reviewed-record-guide.pdf \
  --output publication-review --copy-carrier
```

For VAO 0.5, the command requires one `bootstrap` carrier and one
`preservation-closure` carrier. Both must embed byte-identical manifests and pass local
integrity checks plus the VAO 0.5 reference validator. The older one-input VAO 0.4 path
remains available. The command then creates:

```text
publication-review/
├── object-bootstrap.vao             optional copied carrier
├── object-preservation.vao          optional copied carrier
├── README.pdf                       optional reviewed record guide
├── vao-manifest.json                exact carrier manifest bytes
├── vao-release.template.json        schema-valid repository descriptor template
├── zenodo-metadata.template.json    schema-valid legacy projection for review
├── SHA256SUMS
└── publication-readiness.json
```

The release and metadata templates deliberately contain pending repository identity and
restricted-access review values. Their schemas are validated. The metadata document is
a review projection, not a substitute for checking Zenodo's current web form and
rendered draft.

## Recommended record shape

A VAO 0.5 Zenodo record has one semantic release and two transport carriers:

```text
object-bootstrap.vao
object-preservation.vao
vao-manifest.json
vao-release.json
README.pdf
SHA256SUMS
```

The bootstrap is the start-here carrier: it contains the complete semantic manifest and
only the compact evidence needed for inspection. The preservation closure embeds every
realization. Both carriers contain the exact same manifest bytes. The standalone
manifest makes capability discovery cheap, while `vao-release.json` binds the exact
version record, carrier IDs, descriptors, file sizes, and hashes after the repository
identifiers exist. Concept DOIs support discovery; acquisition uses the exact version
DOI, record, file, carrier, member, byte extent, and digest identity.

No separate mobile, production, or preservation records are needed. Those are semantic
realizations inside the same manifest. `vao fetch` can range-retrieve one embedded
member from either carrier, and `vao materialize` can combine any selected subset into a
custom local carrier. This keeps the public record fixed and comprehensible while the
user's local carrier remains purpose-built.

Only these six public files belong at the record root. Measurements, MIDI signal maps,
source workbooks, normalized tables, methods, and acknowledgments belong inside the VAO
as typed realizations and evidence; they are not unpredictable sibling downloads. If a
non-VAO supplementary archive is scientifically necessary, it must be explicitly
documented and inventoried rather than added ad hoc.

## Readiness boundary

The generated `readyForLivePublication` value remains false. A live record still
requires review of:

- creators, contributors, ORCID/ROR identifiers, funding, and subjects;
- rights, licenses, consent, privacy, access, and culturally sensitive material;
- exact Zenodo version and concept identifiers and the final file inventory;
- exact carrier IDs, descriptor hashes, manifest hashes, and `carrier-member`
  distribution targets;
- the current Zenodo metadata model and rendered record preview;
- remote resolution, conformance inspection, selective acquisition, full download, and
  moderated-community submission.

A repository metadata correction must not silently rewrite an existing VAO semantic
manifest. A semantic change creates a new VAO release and normally a new repository
version.
