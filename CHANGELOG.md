# Changelog

## 0.2.0 - 2026-01-06
- Switched CLI to subcommands to include all Docker prune commands: `system`, `image`, `container`, `volume`, `network`.
- Updated `--all/-a` behavior to align with Docker (`system`/`image` affects images; `volume -a` includes named volumes).
- Removed `docker system df` based totals; tool now reports an estimate based on items listed.

## 0.1.3 - 2026-01-05
- Updated README with PyPI/license badges and promoted `pip install docker-prune-plan` as the recommended install path.

## 0.1.2 - 2026-01-05
- First public release on PyPI.
- Updated project metadata (authors, classifiers, URLs) for public distribution.
- Added MIT License.

## 0.1.1 - 2025-12-27
- Fix reclaimable totals to respect `--type` and `--volumes` filters, matching `docker system df` output to the chosen scope.

## 0.1.0 - 2025-12-27
- Initial release.
