VAO CLI 0.4.1 validates VAO 0.4.0, 0.5.0, and 0.6.0 with the signed
VAO Standard v0.6.0 checkout. Validation reports the result of each declared
MMIC 1.0.0 level and identifies any inputs that still need to be retrieved.

For a two-carrier VAO 0.5.0 or 0.6.0 release, the CLI can fetch one realization
from the preservation carrier by HTTP range and verify its bytes. A custom
carrier keeps the original manifest. The VAO 0.6.0 repository workflow was
tested against a simulated Zenodo record.

Offline publication staging supports VAO 0.4.0 and 0.5.0. Metadata revision is
available for VAO 0.4.0. Zenodo access is read-only.

The build and release lock updates certifi, setuptools, and urllib3.

Install the attached wheel with Python 3.11 or newer and follow the setup
instructions in the [README](https://github.com/modavis-project/vao-cli/tree/v0.4.1).
