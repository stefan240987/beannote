# Changelog

All notable BeanNote changes are recorded here.

## [1.4.2] - 2026-08-23

- Replaced the WebRTC `st.camera_input` frame with a native `st.file_uploader` (JPG, JPEG, PNG, HEIC, WebP) so Safari, Chrome, Firefox, Edge, and Samsung Internet on iOS and Android open the OS camera / photo-library picker instead of getUserMedia.
- Scan UI is a mobile action card (`📸 Scan kaffepose`) with a dashed terracotta dropzone and 48–52px thumb targets.
- Camera and album photos still go through the same Gemini 1.5 Vision extraction + fuzzy-match pipeline; uploads are normalized to JPEG before scan and storage.

## [1.4.0] - 2026-08-23

- Gemini Vision now writes a short Danish "Kaffens Historie" from label facts plus coffee knowledge (farm, region, altitude, varietals, flavor narrative).
- `beans.story` column with a safe migration; the scan-result editor and Explore detail modal show the story as a journal section.

## [1.3.0] - 2026-08-23

- Gemini 1.5 Flash Vision scans coffee-bag photos and returns structured fields (roaster, bean name, origin, process, roast level, flavor tags, official notes), then pre-fills the add-bean form including matching `st.multiselect` tags and `st.selectbox` values.
- Local `.env` is created automatically; `GEMINI_API_KEY` is read from the environment or Streamlit secrets. `.env` and `.streamlit/secrets.toml` stay gitignored.
- Unraid / Docker: `docker-compose.yml` passes `GEMINI_API_KEY=${GEMINI_API_KEY}` so the key can be set in the Docker GUI. Tesseract remains the offline fallback.

## [1.1.0] - 2026-08-23

- Flavor radar labels (Syre, Sødme, Krop, Eftersmag) stay fully visible: inset polar domain, generous plot margins, overflow-visible chart containers, and a taller 400px plot. The chart stays frozen (`staticPlot`) on desktop and touch.
- Detail dialog uses Streamlit `width="large"` plus fluid CSS — near full-width on phones, a centered ~720px card on desktop — with bag photos scaled via `object-fit: contain` so they never stretch or shove content off-screen.
- Flavor badges remain official 1–2 word descriptors only; sentence fragments and punctuated OCR leftovers never render as pills.
- Mobile-first spacing: 14–16px safe-area gutters, 44–48px touch targets, and calmer tab/card padding.

## [1.0.8] - 2026-08-23

- Plotly flavor radar is fully static (`staticPlot`, no mode bar) so it cannot be dragged, zoomed, or pinched on desktop or touch.

## [1.0.7] - 2026-08-23

- Flavor pills are official 1–2 word descriptors only (`Mørk chokolade`, `Karamel`, `Blåbær`, `Citrus`); overlapping generics such as `Chokolade` are dropped when a more specific tag is present.
- Explore cards are fully clickable: bag photos fill the card top at `200px` / `object-fit: cover`, and the detail modal is `95vw` on mobile with a contained `250px` hero image.
- Detail compare view uses clean flavor badges instead of long-sentence pills; radar chart padding is reduced so labels stay on-screen on phones.

## [1.0.6] - 2026-08-23

- Camera-first mobile flow: `📸 Scan pose & Rate` opens `st.camera_input` / upload, runs OCR + fuzzy match immediately, and jumps to Rate when the match is ≥85% (`Bønne fundet i BeanNote!`) or pre-fills Add Bean with the snapped photo when it is not.
- Rating sliders (overall, acidity, sweetness, body, aftertaste) snap to half-steps only (`step=0.5`).
- Bag photos persist on `beans.image_url` and render at the top of Explore cards (`object-fit: cover; height: 160px`) with a coffee-illustration fallback.
- Mobile CSS cleanup: 10px container padding, thumb-sized nav tabs, and 16px touch dropdowns to avoid overflow and iOS zoom.

## [1.0.5] - 2026-08-23

- Localized help tooltips on rating sliders (overall, acidity, sweetness, body, aftertaste).
- Smart OCR matching: exact (≥90%) hard-blocks duplicates and jumps to Rate with extracted notes; near (70–89%) warns but allows save-as-new; below 70% treats the scan as a new bean.

## [1.0.4] - 2026-08-23

- OCR bean-name parser ignores graphic noise (`ler oil`, `Est.`, `SEDATO`, `A POSEN`) and prefers title candidates (`Crema`, `Slow Roast`, `Geisha`, `Yirgacheffe`) in the upper/middle block.
- Fuzzy origin matching maps OCR typos such as `ETIDPIEN` → Ethiopia and combines blends (`BRASILIEN & ETIDPIEN` → `Brazil / Ethiopia`).
- Standalone process lines (`NATURAL`, `WASHED`, `VASKET`, `ANAEROBIC`, `HONEY`) pre-select the process dropdown.
- Flavor-tag matching always includes `Citrus` alongside `Mørk chokolade`, `Karamel`, and `Blåbær`.

## [1.0.3] - 2026-08-23

- Advanced Danish label OCR: roaster lines (`ROASTER`, `COFFEE`, `MIKRORISTERI`, `BREW`, `EST.`) vs standalone bean headers (`Crema`, `Slow Roast`).
- Parse `OPRINDELSE`/`ORIGIN` (e.g. Brasilien & Etiopien → Brazil / Ethiopia), `FORARBEJDNING`/`PROCESS`, and ristningsgrad (`Lys` / `Medium` / `Mørk`).
- Extract `Noter af` / `Smag af` / `Tasting notes` and pre-select matching smagsnoter in the add-bean multiselect.

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
