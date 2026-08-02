---
name: Reproducibility mismatch
about: You rebuilt wallet.zip and got a different hash from the one published
labels: reproducibility
---

The whole point of this project is that you can check it rather than trust it, so
a mismatch is a real report, not a nuisance. Thank you for looking.

**Hashes**
- `wallet_zip_sha256` you got:
- `wallet_zip_contents_sha256` you got:
- The values in `UPSTREAM` at the commit you built:

If the *contents* hash matches but the zip hash does not, the two builds hold the
same files and differ only in compression — worth reporting, but not a code
difference.

**Your environment**
- OS and version:
- Python version:
- `git rev-parse HEAD` in this repo:

**Anything else**
Output of the build script, if you still have it.
