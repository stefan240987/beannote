# Changelog

All notable BeanNote changes are recorded here.

## [2.6.0] - 2026-08-23

- Explore feed cards are minimal: bag photo (180px, cover), favorite heart, name, roaster · origin, 2–3 flavor badges, and the average star. Leaflet maps, radar charts, recipes, and MASL/varietal metadata no longer render on the card.
- Bean details open in a dedicated modal: hero image, info + collapsible story, brew recommendations and My Recipe, a contained `h-44` origin map, and a static 0–5 flavor radar.
- World Map view hides the card grid and shows one full-viewport Leaflet map with ☕ pins; tapping a pin opens that bean’s modal.
- Local/dev `index.html` is served with `Cache-Control: no-store`, and `python main.py` binds Uvicorn to `0.0.0.0:8501` for phone testing on the same Wi-Fi.

## [2.5.0] - 2026-08-23

- Local mobile testing: Uvicorn binds `0.0.0.0:8501`, CORS allows all origins, and startup/`run_local.sh` print the LAN URL plus a terminal QR code so a phone on the same Wi-Fi can open BeanNote immediately.
- Full i18n for new UI (favorites filters, map toggles, recipe fields, map popups, toasts). Gemini Vision prompts now receive the active `da`/`en` language; the Profile tab switcher still persists in `localStorage`.
- Favorites table (`user_id`, `bean_id`) with a heart toggle on every coffee card and detail modal, plus an Explore filter for All Beans / Favorites.
- Enthusiast metadata (`roast_date`, `altitude`, `varietal`, `latitude`, `longitude`, `region_full`) from Gemini Vision, shown as MASL / varietal badges with localized brew recommendations. Ratings store personal brew parameters as My Recipe.
- Leaflet origin map in the AI preview and detail modal (☕ pin) plus an Explore Cards / World Map toggle for all saved beans.
- `ENVIRONMENT=local` flushes every SQLite table on startup for clean test runs.

## [2.1.0] - 2026-08-23

- Local/test startup now performs an absolute wipe of `beans`, `ratings`, and `users` (plus bag photos) when `ENVIRONMENT=local` or `RESET_DB_ON_START=true`. Demo beans are no longer seeded, so every test run starts empty.
- Gemini Vision extracts a `brew_recommendation` (`recommended_method`, `grind_size`, `water_temp`, `brew_ratio`) and BeanNote infers one from roast/origin/process when the bag is silent. Fields persist on `beans` and render as `💡 Bryganbefalinger` next to `📖 Historie`.
- Local Google/Apple buttons instant-sign-in with test profiles (`google_test_user@beannote.local`, `apple_test_user@beannote.local`). Production still uses OAuth client-ID hooks.

## [2.0.0] - 2026-08-23

- Replaced Streamlit with a FastAPI backend (`main.py`) and a Tailwind CSS PWA (`static/index.html`) served by Uvicorn on port 8501.
- Added email/password registration and login plus Google OAuth and Apple Sign-In. Sessions use signed JWTs in HTTP-only cookies, with optional `Authorization: Bearer` tokens.
- `users` table (`id`, `email`, `username`, `password_hash`, `auth_provider`, `oauth_id`, `created_at`); ratings now store `user_id`.
- Local/test SQLite is wiped and re-seeded on startup when `ENVIRONMENT=local` or `RESET_DB_ON_START=true`. Production (`ENVIRONMENT=production`) never auto-flushes, so Unraid data stays intact.
- Camera-first scan uses native `<input capture="environment">`, Gemini 1.5 Flash Vision with EXIF-upright previews, and a 1-click "✅ Godkend & Gem" approval card. Flavor radar is a static 0–5 Chart.js chart.
- `run_local.sh` and Docker now launch `uvicorn main:app`; health checks use `/api/health`.

## [1.5.0] - 2026-08-23

- Camera and album uploads are auto-rotated with `PIL.ImageOps.exif_transpose` before Gemini/Tesseract and before any preview, so portrait phone photos stay upright.
- After an AI scan, Add Bean shows a compact Detection Summary card (photo, name, roaster, origin, flavor pills, expandable story) and one primary action: "✅ Godkend & Gem til BeanNote".
- The long stacked editor stays behind "✏️ Ret oplysninger"; the scan dropzone is hidden during approval so the card and button fit on a phone screen.

## [1.4.4] - 2026-08-23

- Autonomous env verification: `.env` is created or repaired, `GEMINI_API_KEY` is loaded into `os.environ` and mirrored to `.streamlit/secrets.toml`, and Gemini 1.5 Flash is pinged before any Tesseract fallback.
- When a Gemini key is present, bag scans stay on Vision only — noisy Tesseract OCR is never used as a fallback.
- Single unified scanner (`st.file_uploader`) on the scan card; the duplicate add-tab uploader and the "Rå scan-data" debug expander are gone.
- Localized dropdown placeholders (e.g. "Vælg smagsnoter...") and a 140px `object-fit: cover` upload preview so mobile form fields stay on screen.

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
