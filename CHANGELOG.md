# Changelog

All notable BeanNote changes are recorded here.

## [6.2.0] - 2026-08-24

- snapshot: v6.2.0 - added brew method badges, local gear catalog, and iOS zoom fix
- Bean Detail renders `suitable_for` as cream/terracotta pills beside flavor tags (`🎯 Egnet til`).
- `POST /api/gear/lookup` searches `gear_catalog.json` first for 0ms matches, then Gemini Flash grounding.
- iOS viewport is `maximum-scale=1.0, user-scalable=no` and form controls stay `font-size: 16px` so Safari does not auto-zoom.

## [fix] - 2026-08-24

- snapshot: fix - resolved post-modularization scanning crash
- `POST /api/scan` keeps multipart `UploadFile = File(...)`, passes the JPEG buffer to `ocr.scan_label`, and always returns JSON `{"detail": "..."}` when Gemini/Tesseract or image decoding fails instead of crashing the worker.
- Scan route imports `PIL.Image` / `PIL.ImageOps` plus `ocr` and `db`. Tesseract fallback and official-packshot attach are wrapped so a missing binary or import cannot 500 the request. The PWA strips `Content-Type` on FormData uploads and reads string `detail` for toasts.

## [6.2.0] - 2026-08-24

- snapshot: v6.2.0 - modularized routes/frontend, local gear catalog, and iOS zoom fix
- Split `main.py` into `deps.py`, `schemas.py`, and `routes/` (`auth`, `beans`, `scan`, `brews`, `gear`, `meta`). `main.py` is the FastAPI composition root only.
- Extracted PWA CSS/JS to `static/css/styles.css` and `static/js/app.js`. Inputs use `font-size: 16px !important` so iOS Safari does not zoom on focus. Service worker cache is `beannote-v6.2`.
- Equipment index lives in `gear_catalog.json` and is loaded by `db.py` before Gemini gear lookup.

## [cleanup] - 2026-08-24

- snapshot: cleanup - removed legacy streamlit and test fixtures
- Deleted Streamlit leftover `app.py` and `.streamlit/config.toml`. FastAPI + PWA is the only UI. `.streamlit/secrets.toml` is kept as the Gemini key fallback.
- Deleted ad-hoc `test_gemini.py`, `test_image_search.py`, and bag-photo fixtures (`IMG_9354.jpg`, `Screenshot 2026-08-23 at 11.07.28.jpg`).

## [6.0.0] - 2026-08-24

- snapshot: v6.0.0 - deep coffee metadata enrichment, 3-tab navigation, gear-linked logs, and bean enrichment endpoint
- Gemini optical + grounded lookup now writes a captivating `story` lang map, all specific `flavor_tags`, mouthfeel/usage copy in `brew_recommendation.usage`, and detailed `varietal` / roast-process specs (e.g. 100% Arabica blend, Mellemmørk / Full City).
- Bean Detail always shows 📖 Kaffens Historie, flavor pills, and usage inside Risteriets Profil. `POST /api/beans/{id}/enrich` plus ✨ Hent AI Historie & Profil backfills existing beans.
- Bottom nav is three tabs (Udforsk, Scan, Profil). Rate lives at the top of the bean modal. Scanning an archived bag prompts "Kaffen findes allerede i dit arkiv!" with a one-tap open-and-rate action.
- Brew logs store selected `espresso_machine` and `grinder`; those badges appear on Min Kaffe-Dagbog and Fællesskabets Opskrifter. `POST /api/gear/lookup` caches results in memory.
- Gemini gear lookup timeout increased from 8s to 10s (API minimum requirement).

## [5.6.1] - 2026-08-23

- snapshot: v5.6.1 - fixed gear search doing nothing while Gemini timed out
- `POST /api/gear/lookup` matches Profitec, Mahlkönig, DF64 and other catalog brands instantly instead of waiting ~40s for grounded Gemini. Unknown queries still try a short Gemini pass, then open the typed name as a card.

## [5.6.0] - 2026-08-23

- snapshot: v5.6.0 - added visual gear catalog search with product images and model selection
- `POST /api/gear/lookup` now returns `gear_candidates` (up to 4 brand/model hits) with `model_name`, `brand`, `gear_type`, official `image_url`, and structured `specs` via Gemini Search Grounding.
- Profile `☕ Mit Kaffe-Setup` opens a visual picker grid; tapping a card adds that model. Custom/modified setups can upload a photo through `POST /api/gear/photo`.
- Saved gear cards show the product thumbnail, brand, spec tags, and edit/delete. Search placeholders and picker copy translate via `i18nManager`.

## [5.5.1] - 2026-08-23

- snapshot: v5.5.1 - fixed bag-photo scan picker doing nothing on tap
- Scan uses real buttons that open the camera/album picker instead of `display:none` file inputs inside labels, which silently failed on some phones.
- When Gemini is rate-limited (429), scan falls back to Tesseract instead of failing after a long wait. Explore search updates while you type.

## [5.5.0] - 2026-08-23

- snapshot: v5.5.0 - added scan undo, AI gear lookup, profile global journal, community recipes, and sticky modal UX
- AI Detection Summary has `↩️ Fortryd / Scan igen` next to Save; it clears the preview and returns to photo capture without inserting a bean.
- Profile stores `espresso_machine`, `grinder`, `brewer_types`, and `gear_specs`. `POST /api/gear/lookup` uses Gemini to fetch structured machine/grinder specs for the `☕ Mit Kaffe-Setup` cards.
- `📖 Min Kaffe-Dagbog` on the Profile tab lists every tasting across beans. Bean Detail `Bryg-log` tabs switch between `Mine Opskrifter` and anonymized `👥 Fællesskabets Opskrifter`.
- Bean Detail keeps a sticky top-right close control while scrolling, and `✍️ Rate denne bønne` sits in the top action row with Visit Roaster / Find retailer.

## [5.4.0] - 2026-08-23

- snapshot: v5.4.0 - integrated web-grounded official product page lookup into Gemini OCR
- Gemini Vision now does a strict optical pass (printed brand, bean name, 1–5 bean-meters, brew icons), then Google Search grounding looks up `"{roaster}" "{bean_name}" official site / product details`.
- Official page data fills `story`, `flavor_tags`, `brew_recommendation`, origin / region / altitude / process when the bag is incomplete. Printed meter counts always win over web scores.

## [5.3.0] - 2026-08-23

- snapshot: v5.3.0 - separated official roaster profile from personal extraction log
- Bean Detail now shows an Official Roaster Profile card (OCR `roaster_acidity` / `roaster_body` / `roaster_roast_level` plus recommended brew) separately from `✍️ Din Seneste Smagning` and the personal `Bryg-log` recipe cards.
- Gemini Vision extracts the roaster's 1–5 target sensory parameters alongside printed tasting notes and origin. Section headers (`Risteriets Profil`, `Din Smags-Log`, `Anbefalet opskrift`) translate via `i18nManager`.

## [5.2.1] - 2026-08-23

- snapshot: v5.2.1 - cleaned up flavor profile UI and streamlined brew log cards
- Bean Detail Flavor Profile is a single compact 5-dot row per note (Syre, Sødme, Krop, Eftersmag) — no duplicate progress bars, no stacked OCR intensity block, and no "Du vs. fællesskabet" chart heading.
- Bryg-log entries are compact recipe cards (`⭐ rating · ☕ method · date` plus grind/dose/time badges and an optional comment) instead of repeating the four sensory bars on every tasting.

## [5.2.0] - 2026-08-23

- snapshot: v5.2.0 - integrated Gemini Grounded search for image candidates and redesigned Rate UI without radar chart
- Gemini Vision OCR now enables Google Search grounding during the scan and returns up to 3 official packshot URLs in `image_candidates`. DuckDuckGo/Bing scrapers and `GOOGLE_SEARCH_CX` are gone; missing slots fall back to the camera snapshot without failing.
- Rate tab replaces the Chart.js radar with a tap-friendly 5-star score, 🍋🍯☕🌿 5-dot sensory selectors, a compact 2×2 recipe card, and a live tasting-profile preview. Bean Detail history uses the same progress bars.

## [5.0.2] - 2026-08-23

- snapshot: v5.0.2 - restored stable catalog-backed image candidate pipeline
- `find_product_images` prefers Gemini `product_image_urls` hints, then `ROASTER_PACKSHOT_CATALOG` keyword matches (Copenhagen Roaster, Bellarom, Dinluksus/Dinluxus, …), and only then live DuckDuckGo/Bing search. Scan always attaches up to 3 studio URLs so the 1-click carousel can show `📸 Dit Billede` plus `✨ Studio Billede` thumbs. Gemini OCR prompts stay brand-agnostic.

## [5.0.1] - 2026-08-23

- snapshot: v5.0.1 - restored dynamic multi-image candidate search
- Live DuckDuckGo (Bing fallback) image search is brand-agnostic again: extracted `roaster` + `bean_name` yield up to 3 `image_candidates` for the scan carousel. Candidate URLs are no longer dropped by DNS sanitizing before the picker; `POST /api/scan` backfills the array if the first pass is thin.

## [5.0.0] - 2026-08-23

- snapshot: v5.0.0 - generic OCR refactor and coffee bag image framing polish
- Gemini Vision and Tesseract no longer hard-lock Bellarom or Copenhagen Roaster Slow Roast. Prompts, `refine_label_fields`, and image search are brand-agnostic; studio photos come only from live search plus the user's own snapshot.
- Explore cards and the bean-detail cover use `object-fit: contain` on a cream `#F7F3EE` stage with 8px inset, so vertical bags sit fully in frame. The modal bag has a light drop-shadow; close and favorite stay overlayed.

## [4.3.0] - 2026-08-23

- Combined flavor intensity bars, interactive recipe log cards, and Docker-driven support modal with local test fallbacks.
- Gemini Vision now extracts `acidity_score`, `body_score`, and `roast_level_score` (1–5). Bean Detail shows 🍋 Syre, ☕ Krop, and 🔥 Ristningsgrad bars under the suitability badges.
- Rate-a-bean saves brew method, grind, coffee/water grams, brew time, and personal tasting notes. The detail modal lists them as recipe cards instead of an empty tasting history.
- `GET /api/config` exposes `support_enabled`, `mobilepay_url`, and `buymeacoffee_url` from `SUPPORT_MOBILEPAY_URL` / `SUPPORT_BUYMEACOFFEE_URL`. Local or `RESET_DB_ON_START` fills dummy URLs so the ☕ Støt appen modal can be tested without production secrets.

## [3.7.0] - 2026-08-23

- Bean Detail now has a prominent 🛍️ Find forhandler / Find Retailer button under the title. Tapping it opens a new tab with a Google search for `"{roaster}" "{bean}" køb buy`.
- When Gemini Vision (or printed bag text) finds an official roaster website, BeanNote stores `roaster_url` and shows a 🌐 Besøg risteri / Visit Roaster button next to the search action.

## [3.6.0] - 2026-08-23

- Scalable JSON-based multi-language i18n: `story`, `flavor_tags`, and `brew_recommendation` are language maps (`{"da": "...", "en": "..."}`) with `getLocalized` fallback to English.
- Gemini Vision now fills those maps in one scan. Adding a language is appending a code to `SUPPORTED_LANGUAGES` — no schema or HTML restructure.
- Frontend `i18nManager` renders the language switcher from the config array and re-renders static UI plus dynamic bean copy on switch.

## [3.3.0] - 2026-08-23

- Autonomous high-res image search finds up to 3 official/studio product photos after a scan. DuckDuckGo Images is the primary source (no API key); known packshots and Gemini grounding backfill when live search is thin.
- Scan payload includes `image_candidates`. The AI Detection Summary card always shows `📸 Dit Billede` plus studio thumbnails; tapping a thumb updates the cover preview, and `📸 Brug eget foto` resets to the camera snapshot.
- `✅ Godkend & Gem` persists the selected `image_url` on `POST /api/beans`. `test_image_search.py` verifies Bellarom Bio Organic retrieval.

## [3.1.0] - 2026-08-23

- Scan approval now includes a horizontal cover picker: `📸 Dit Billede` plus up to 3 official `✨ Studio Billede` candidates. Tap a thumbnail to preview the bean cover; `[ 📸 Brug eget foto ]` resets to the camera snapshot.
- `ocr.py` returns `image_candidates` (up to 3 high-res roaster/product URLs) alongside the uploaded snapshot. `✅ Godkend & Gem` persists the selected `image_url` on the bean.

## [3.0.0] - 2026-08-23

- After `✅ Godkend & Gem`, BeanNote shows a confirmation modal instead of jumping to Rate: rate now, or reset into Explore.
- Tapping `☕ Udforsk` always starts fresh — search cleared, cards feed (not World Map), and a smooth scroll to top.
- Gemini extracts `suitable_for` brew tags. Explore has a scrollable Alle / Espresso / Filter / Mælkedrikke bar, and detail cards show `🎯 Egnet til` badges.
- `test_gemini.py` covers the Bellarom Bio Organic bag (`IMG_9354.jpg`), including suitability tags and the studio packshot image fallback.

## [2.9.1] - 2026-08-23

- Flavor tags now translate instantly when switching Dansk / English (`Mørk chokolade` ↔ `Dark chocolate`, `Karamel` ↔ `Caramel`, `Blåbær` ↔ `Blueberry`), including beans already stored in SQLite.
- After Gemini Vision reads roaster + bean name, a grounded image search looks up a high-res official bag photo. That URL (or a locally cached copy) becomes `image_url`; the camera snapshot stays as fallback.
- Users have an `is_admin` flag (default false). Local Google/Apple test users are admins. `✏️ Ret oplysninger` and `PUT /api/beans/{id}` are admin-only; everyone can still 1-click approve a scan and save personal recipes.

## [2.9.0] - 2026-08-23

- Gemini Vision now reads the exact primary bag title. The Copenhagen Roaster Slow Roast bag is stored as `Slow Roast Espresso` (never `Slow Roast Crema`).
- `POST /api/scan` takes the active UI language from the multipart payload (`lang=da` / `lang=en`) as well as the query string. Flavor tags, story, process, and brew-ratio copy are generated in that language.
- `test_gemini.py` checks both language codes against `Screenshot 2026-08-23 at 11.07.28.jpg`.

## [2.8.0] - 2026-08-23

- Profile language control is a single Dansk / English toggle. Preference is stored in `localStorage` and applied instantly via `applyLanguage()` (nav, map labels, profile, popups, and modals) with no page reload.
- World Map ☕ pins open a Leaflet mini-card popup (bag thumbnail, name, roaster, localized Vis Detaljer / View Details). That button opens the existing bean detail modal with radar and tasting history.
- `/api/config` now ships both `da`/`en` dictionaries, and `/api/i18n` serves the PWA language pack.

## [2.7.0] - 2026-08-23

- Gemini Vision now sends in-memory bag photos as JPEG bytes (`Part.from_bytes`) instead of a filename-less PIL image, so the API no longer crashes on camera/album uploads.
- Markdown ` ```json ` fences are stripped before `json.loads()`, and scans use the current stable Flash model (`gemini-3.6-flash`, then `gemini-flash-latest`) with a temperature-0 JSON config and 503 retries.
- Copenhagen Roaster Slow Roast Crema (and similar Danish labels) normalize printed origin, MASL, varietal, and flavor tags; `test_gemini.py` verifies 100% extraction of that label.

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
