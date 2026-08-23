# Changelog

All notable BeanNote changes are recorded here.

## [1.0.2] - 2026-08-23

- Fix OCR scan: write parsed fields into Streamlit widget keys and rerun so the add-bean form actually fills.
- Warn when Tesseract finds no text, always show raw OCR, and upscale small label photos.

## [1.0.1] - 2026-08-23

- UI overhaul: compact elevated cards, ghost card actions, iOS-style segmented nav, and a slimmer header with embedded search.
- Flavor tags as soft pills and compact amber `★ 4.0` scores.
- Streamlit chrome overrides for cream canvas, rounded inputs, and primary buttons without default focus rings.

## [1.0.0] - 2026-08-23

- Initial BeanNote release: Explore, Rate, and Add Bean + OCR tabs.
- Dual-environment SQLite paths (`ENVIRONMENT=local` → `./data/beannote.db`, production → `/app/data/beannote.db`).
- Unique `(name, roaster)` constraint plus 80% fuzzy duplicate warning with 1-click jump to the existing bean.
- Plotly radar comparing user vs community for Syre, Sødme, Krop, and Eftersmag.
- Roaster vs user tasting-note tag highlights.
- Local Tesseract label parser, i18n (DA/EN/DE/FR/ES), sidebar CSV/JSON export.
- GHCR multi-arch publish workflow and Unraid-ready `docker-compose.yml`.
