# Local model checkpoint

`demucs/` holds the official **Demucs v4 HTDemucs 6-source** checkpoint used by
Music Cut. It is loaded through Demucs' local-repository mechanism, so the app never
needs to contact Hugging Face at runtime.

The `.th` checkpoint is intentionally **not tracked by Git** (about 55 MB). Running
`setup.command` or starting the app downloads it here and verifies its SHA-256. The
small YAML bag definition is tracked.

| File | Purpose |
| --- | --- |
| `demucs/htdemucs_6s.yaml` | bag definition (`5c90dfd2`) |
| `demucs/5c90dfd2-34c22ccb.th` | PyTorch checkpoint, 54,996,327 bytes |

- Upstream: https://github.com/adefossez/demucs
- Original download: https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/5c90dfd2-34c22ccb.th
- SHA-256: `34c22ccb381c6f9fdbf324f04e1e2fe21aaaf293f5ded163a162697ff9a02ddd`
- License: MIT (Demucs)

The checkpoint is experimental upstream for the piano and guitar sources. See the
project README for the practical implications. Do not edit or rename these files;
`musiccut.model_store` verifies the checksum and can re-download the checkpoint.
