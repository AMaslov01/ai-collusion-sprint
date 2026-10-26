# Validation on 26 October 2026

All scientific execution below occurred on caniculus in nonroot Docker.
No new inference was performed. The OCI digest and selected observed package
versions are in [environment.json](../environment.json).

| Check | Result | Evidence |
| --- | --- | --- |
| Public data-free suite | 175 tests, OK, 3 skips; upstream native-client integration file explicitly excluded | [public-tests.log](public-tests.log) |
| Numeric export from audited raw run | 948 assigned records; every analysis-generated field equals the frozen report | Export command: `scripts/export-public-analysis.py`; hash-bound public input in `release/frozen/analysis-inputs.json` |
| Public arithmetic command | Every analysis-generated field equals the frozen report, NumPy 2.2.6, no model calls | [public-analysis.log](public-analysis.log) |
| Wheel packaging | `memcontrast-0.1.0-py3-none-any.whl` built offline with pinned setuptools 80.10.2 | [wheel-build.log](wheel-build.log) |
| Full production replay for this release | Running; no completion claim yet | Final receipt will replace this row after completion |

The public tests used 4 CPU / 16 GiB, `--network=none`, UID/GID 4200509,
read-only source and container filesystem, no GPU devices. Three skips are the
pinned native-dataset tests; the separate `test_actor_gateway.py` suite needs
the external pinned upstream checkout. Mock transports are not real model calls.

The public arithmetic recomputation reads a numeric projection. It reproduces
all fields returned by the frozen `analyze` function, not the appended
full-run provenance hashes. Whole-file identity of the original report is
checked separately by `scripts/verify-release.py`.
