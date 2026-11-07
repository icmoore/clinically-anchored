# Legal documents

`terms-vX.Y.md` and `privacy-vX.Y.md` are the Terms and the Privacy Notice, word for word from the
approved Doc. `config.json` names the CURRENT version of each, the version and text of each of the two
consent sentences, and the effective date shown on `/terms` and `/privacy`.

To change anything a patient agrees to:

1. Add a NEW file (`terms-v1.1.md`); never edit one that patients have already accepted.
2. Change the version (and `file`) in `config.json`. For a consent sentence, change its `version`
   and `text`.
3. Run `scripts/sync_legal` and commit what it writes. The api keeps its own copy of the versions
   and SHA-256 hashes (it deploys without this folder); a test fails if the copy is out of date.

On the next request every patient whose latest acceptance does not match the current versions is sent
back to the "Before you start" screen.

Before the pilot, run `scripts/check_legal_docs`: it lists any `[square-bracket]` placeholders still
in the current documents.
