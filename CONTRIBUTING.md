# Contributing

Bug reports and focused pull requests are welcome. For substantial behavior or
UI changes, open an issue first so the safety model can be discussed.

1. Fork the repository and create a descriptive branch.
2. Keep cleanup operations dry-run-first, recoverable, and non-overwriting.
3. Run `python -m pytest -q` before submitting a pull request.
4. Explain user-visible behavior and add regression coverage.

Never use personal folders as test fixtures. Tests must operate only on
temporary directories.
