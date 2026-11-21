# Commits cited in the dissertation

This public repository is the project history with one file removed: the private
key that signed the study's certificates (`study/main/STUDY_GATE_KEY`), which was
tracked by mistake between 20 August 2026 and its removal later that day. Removing
it changed the hashes of commits made after that point. The dissertation
(Appendix B) cites the original hashes; this table maps each to its commit here.
Every commit's content, date and message is otherwise unchanged.

| Cited in dissertation | This repository | What it is |
|---|---|---|
| a34fe04 | a34fe04 | Pre-registration of the evaluation study |
| edde921 | edde921 | Fault corpus labels fixed before any trial |
| af77646 | 8195dc7 | Study R registered |
| 2b153fe | 791f9f2 | Study R amendment R2 |
| 86e3288 | bf19975 | Study S registered |
| 2c8249f | f620689 | Study T registered |
| 13a00b3 | 2adf521 | Study T sample frozen |

The tag `corpus-v1-frozen` is present. Certificate chains verify without the key
(hashes and links); certificates issued after the study also carry an Ed25519
signature checkable against the published public key.
