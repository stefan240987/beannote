"""BeanNote — personal coffee journal. Streamlit UI."""

from __future__ import annotations

import base64
import hashlib
import os
from html import escape
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from db import (
    VERSION,
    classify_matches,
    distinct_values,
    export_ratings,
    find_similar_beans,
    get_flavor_profile,
    init_db,
    insert_bean,
    insert_rating,
    list_beans,
    resolve_image_path,
    save_bean_image,
    update_bean_image,
    update_bean_story,
)
from ocr import (
    FLAVOR_NOTES,
    PROCESSES,
    ROAST_LEVELS,
    compare_flavor_notes,
    encode_scan_jpeg,
    ensure_local_env,
    extract_flavor_tags,
    load_local_env,
    normalize_scan_fields,
)
from translations import LANGS, t

ensure_local_env()
load_local_env()

BREW_METHODS = [
    "V60",
    "Espresso",
    "AeroPress",
    "Chemex",
    "French Press",
    "Kalita",
    "Batch Brew",
    "Moka",
    "Cold Brew",
]
ROAST_FILTERS = list(ROAST_LEVELS) + [
    value for value in ("Light", "Medium-Dark", "Dark") if value not in ROAST_LEVELS
]
SCAN_IMAGE_TYPES = ["jpg", "jpeg", "png", "heic", "webp"]
RADAR_CHART_CONFIG = {
    "staticPlot": True,
    "displayModeBar": False,
    "scrollZoom": False,
    "doubleClick": False,
    "displaylogo": False,
    "editable": False,
    "responsive": True,
}
BAG_PLACEHOLDER = """
<div class="bn-card-fallback" aria-hidden="true">
  <svg viewBox="0 0 80 64" width="64" height="52" fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect x="22" y="10" width="36" height="46" rx="5" fill="#faf6f0"/>
    <path d="M22 22h36" stroke="#b85c38" stroke-width="3"/>
    <rect x="32" y="10" width="16" height="6" rx="2" fill="#e8d8c8"/>
    <circle cx="40" cy="40" r="9" stroke="#3c2a21" stroke-width="2"/>
    <path d="M36 39c1.2-3.2 6.8-3.2 8 0" stroke="#b85c38" stroke-width="1.6" fill="none"/>
  </svg>
</div>
"""

st.set_page_config(
    page_title="BeanNote",
    page_icon="☕",
    layout="wide",
    initial_sidebar_state="expanded",
)


def inject_css() -> None:
    st.markdown(
        """
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Outfit:wght@400;500;600;700&display=swap" rel="stylesheet">
        <style>
        :root {
            --espresso: #3c2a21;
            --terracotta: #b85c38;
            --cream: #faf6f0;
            --foam: #f3ebe3;
            --latte: #e8d8c8;
            --ink: #2c221e;
            --muted: #8c7a6b;
            --line: #eae3d9;
            --input-line: #e0d6cc;
            --chip: #f4ebd9;
            --amber: #d97706;
        }
        html {
            -webkit-text-size-adjust: 100%;
            text-size-adjust: 100%;
        }
        html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"] {
            background: #faf6f0;
            color: var(--espresso);
            font-family: "Outfit", "Avenir Next", sans-serif;
            overflow-x: hidden;
            overscroll-behavior-x: none;
        }
        [data-testid="stHeader"] { background: transparent; }
        #MainMenu, footer, [data-testid="stToolbar"] { visibility: hidden; }
        .block-container {
            padding-top: 0.75rem;
            padding-bottom: max(3.5rem, env(safe-area-inset-bottom));
            padding-left: max(1rem, env(safe-area-inset-left));
            padding-right: max(1rem, env(safe-area-inset-right));
            max-width: 1100px;
        }
        h1, h2, h3, .bn-brand {
            font-family: "Fraunces", Georgia, serif;
            color: var(--espresso);
            letter-spacing: -0.02em;
        }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #3c2a21 0%, #2a1c16 100%);
        }
        [data-testid="stSidebar"] * { color: #faf6f0 !important; }
        [data-testid="stSidebar"] .stSelectbox div[data-baseweb="select"] > div,
        [data-testid="stSidebar"] .stSlider { color: #3c2a21 !important; }
        [data-testid="stSidebar"] .stDownloadButton button,
        [data-testid="stSidebar"] .stButton button {
            background: #b85c38;
            color: #faf6f0 !important;
            border: 0;
            border-radius: 8px;
            min-height: 40px;
            font-weight: 600;
        }
        .bn-hero {
            background: linear-gradient(135deg, #3c2a21 0%, #4a3328 72%, #b85c38 155%);
            color: #faf6f0;
            border-radius: 14px;
            padding: 0.85rem 1.05rem 0.8rem;
            margin-bottom: 0.65rem;
            box-shadow: 0 8px 20px rgba(60, 42, 33, 0.12);
        }
        .bn-kicker {
            font-family: "Outfit", sans-serif;
            font-size: 11px;
            letter-spacing: 1.4px;
            text-transform: uppercase;
            font-weight: 600;
            opacity: 0.68;
            margin: 0 0 0.15rem;
        }
        .bn-hero h1 {
            color: #faf6f0;
            margin: 0 0 0.15rem;
            font-size: 1.45rem;
            font-weight: 700;
            letter-spacing: -0.03em;
        }
        .bn-hero p { margin: 0; opacity: 0.78; font-size: 0.86rem; font-weight: 400; }
        .bn-card {
            background: #fffdf9;
            border: 1px solid #eae3d9;
            border-radius: 12px;
            padding: 0.75rem 0.85rem 0.65rem;
            box-shadow: 0 4px 12px rgba(0,0,0,0.03);
            overflow: hidden;
            max-width: 100%;
        }
        .bn-card { cursor: pointer; }
        .bn-card.has-photo { padding: 0; }
        .bn-card.has-photo .bn-card-body { padding: 0.75rem 0.85rem 0.65rem; }
        .bn-card-under-photo {
            border-top: 0;
            border-radius: 0 0 12px 12px;
        }
        .bn-card-fallback {
            height: 200px;
            border-radius: 12px 12px 0 0;
            background: linear-gradient(160deg, #4a3328 0%, #3c2a21 58%, #b85c38 145%);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
        }
        .bn-bag-img,
        .bn-card.has-photo img.bn-bag-img {
            height: 200px !important;
            width: 100% !important;
            object-fit: cover !important;
            border-radius: 12px 12px 0 0;
            cursor: pointer;
            display: block;
        }
        .bn-card-cta {
            margin-top: 0.4rem;
            font-size: 12px;
            font-weight: 600;
            color: #b85c38;
        }
        .bn-match-box {
            background: #fff4e8;
            border: 1px solid #e2b089;
            border-radius: 12px;
            padding: 0.65rem 0.8rem;
            margin: 0.55rem 0 0.2rem;
        }
        .bn-story {
            background: linear-gradient(180deg, #faf6f0 0%, #f4ebd9 100%);
            border: 1px solid #e2b089;
            border-left: 4px solid #b85c38;
            border-radius: 12px;
            padding: 0.9rem 1rem 0.95rem;
            margin: 0.75rem 0 0.9rem;
            box-shadow: 0 1px 0 rgba(60, 42, 33, 0.05);
        }
        .bn-story-kicker {
            font-family: "Fraunces", Georgia, serif;
            font-size: 15px;
            font-weight: 700;
            color: #3c2a21;
            letter-spacing: -0.01em;
            margin: 0 0 0.45rem;
        }
        .bn-story p {
            margin: 0;
            font-family: "Fraunces", Georgia, serif;
            font-size: 14.5px;
            line-height: 1.6;
            color: #4a3328;
            font-weight: 500;
        }
        .bn-story-head {
            display: flex;
            align-items: center;
            gap: 0.4rem;
            font-family: "Fraunces", Georgia, serif;
            font-size: 1.05rem;
            font-weight: 700;
            color: #3c2a21;
            letter-spacing: -0.01em;
            margin: 0.85rem 0 0.2rem;
        }
        [data-testid="stColumn"]:has(.bn-card-hit),
        [data-testid="column"]:has(.bn-card-hit) {
            position: relative;
        }
        [data-testid="stColumn"]:has(.bn-card-hit):hover .bn-card,
        [data-testid="column"]:has(.bn-card-hit):hover .bn-card {
            border-color: #d7c4b4;
            box-shadow: 0 8px 18px rgba(60, 42, 33, 0.08);
        }
        [data-testid="stColumn"]:has(.bn-card-hit) [data-testid="stElementContainer"]:has([data-testid="stButton"]),
        [data-testid="column"]:has(.bn-card-hit) [data-testid="stElementContainer"]:has([data-testid="stButton"]),
        [data-testid="stColumn"]:has(.bn-card-hit) [data-testid="stButton"],
        [data-testid="column"]:has(.bn-card-hit) [data-testid="stButton"] {
            position: absolute !important;
            inset: 0 !important;
            z-index: 2;
            width: 100% !important;
            height: 100% !important;
            min-height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
        }
        [data-testid="stColumn"]:has(.bn-card-hit) [data-testid="stButton"] button,
        [data-testid="column"]:has(.bn-card-hit) [data-testid="stButton"] button {
            position: absolute !important;
            inset: 0 !important;
            width: 100% !important;
            height: 100% !important;
            min-height: 100% !important;
            opacity: 0 !important;
            cursor: pointer !important;
            background: transparent !important;
            border: 0 !important;
            box-shadow: none !important;
        }
        [data-testid="stDialog"] [role="dialog"],
        [data-testid="stModal"] [role="dialog"],
        div[role="dialog"] {
            width: min(720px, 92vw) !important;
            max-width: min(720px, 92vw) !important;
            padding: 1.15rem 1.35rem 1.25rem !important;
            border-radius: 16px !important;
        }
        .bn-modal-hero {
            display: flex;
            justify-content: center;
            align-items: center;
            margin: 0 auto 0.75rem;
            max-height: min(280px, 34vh);
            background: transparent;
        }
        .bn-modal-img,
        [data-testid="stDialog"] img,
        [data-testid="stModal"] img,
        div[role="dialog"] img {
            max-height: min(280px, 34vh) !important;
            width: auto !important;
            max-width: 100% !important;
            height: auto !important;
            object-fit: contain !important;
            display: block;
            margin: 0 auto;
            border-radius: 12px;
        }
        [data-testid="stImage"] img {
            object-fit: contain !important;
            max-width: 100% !important;
            height: auto !important;
        }
        [data-testid="stPlotlyChart"],
        [data-testid="stPlotlyChart"] > div,
        .js-plotly-plot,
        .plot-container,
        .svg-container {
            max-width: 100% !important;
            overflow: visible !important;
            touch-action: none !important;
            user-select: none !important;
            -webkit-user-select: none !important;
        }
        [data-testid="stPlotlyChart"] {
            padding: 0.15rem 0.35rem 0.35rem;
            min-height: 400px;
        }
        .bn-scan-slot { margin: 0 0 0.7rem; }
        .bn-scan-card {
            background: #fffdf9;
            border: 1px solid #eae3d9;
            border-bottom: 0;
            border-radius: 16px 16px 0 0;
            padding: 1rem 1.05rem 0.15rem;
            margin: 0;
            box-shadow: 0 6px 18px rgba(60, 42, 33, 0.06);
        }
        [data-testid="stElementContainer"]:has(.bn-scan-card) {
            margin-bottom: 0 !important;
            padding-bottom: 0 !important;
        }
        [data-testid="stElementContainer"]:has(.bn-scan-card) + [data-testid="stElementContainer"] {
            background: #fffdf9;
            border: 1px solid #eae3d9;
            border-top: 0;
            border-radius: 0 0 16px 16px;
            padding: 0 0.85rem 0.95rem;
            margin: 0 0 0.85rem;
            box-shadow: 0 6px 18px rgba(60, 42, 33, 0.06);
        }
        .bn-scan-title {
            font-family: "Fraunces", Georgia, serif;
            font-size: 1.2rem;
            font-weight: 700;
            color: #3c2a21;
            letter-spacing: -0.02em;
            margin: 0 0 0.3rem;
        }
        .bn-scan-sub {
            margin: 0 0 0.85rem;
            color: #8c7a6b;
            font-size: 0.9rem;
            line-height: 1.45;
            font-weight: 400;
        }
        [data-testid="stVerticalBlock"]:has(.bn-scan-card) [data-testid="stFileUploader"] section,
        [data-testid="stFileUploaderDropzone"] {
            min-height: 108px !important;
            border-radius: 12px !important;
            border: 1.5px dashed #b85c38 !important;
            background: #faf6f0 !important;
            box-shadow: none !important;
            padding: 1rem 0.85rem !important;
            touch-action: manipulation;
        }
        [data-testid="stFileUploader"] button,
        [data-testid="stFileUploaderDropzone"] button {
            background: #b85c38 !important;
            color: #faf6f0 !important;
            border: 0 !important;
            border-radius: 10px !important;
            min-height: 48px !important;
            min-width: 160px !important;
            padding: 0.7rem 1.25rem !important;
            font-weight: 600 !important;
            font-size: 15px !important;
            touch-action: manipulation;
        }
        [data-testid="stFileUploader"] button:hover,
        [data-testid="stFileUploaderDropzone"] button:hover {
            background: #9a4b2d !important;
            color: #fff !important;
        }
        .bn-roaster {
            font-size: 11px;
            letter-spacing: 1px;
            color: #8c7a6b;
            font-weight: 600;
            text-transform: uppercase;
            font-family: "Outfit", sans-serif;
        }
        .bn-card h3, .bn-name {
            font-family: "Outfit", "Avenir Next", sans-serif;
            margin: 0.18rem 0 0.2rem;
            font-size: 16px;
            font-weight: 700;
            color: #2c221e;
            letter-spacing: -0.01em;
        }
        .bn-meta { color: #8c7a6b; font-size: 12px; margin: 0.15rem 0 0.4rem; }
        .bn-stars {
            color: #d97706;
            font-size: 13px;
            font-weight: 600;
            letter-spacing: 0;
            font-family: "Outfit", sans-serif;
        }
        .bn-badges {
            display: flex;
            flex-wrap: wrap;
            gap: 0.28rem;
            margin: 0.15rem 0 0.25rem;
        }
        .bn-badge {
            display: inline-flex;
            align-items: center;
            background-color: #f4ebd9;
            color: #3c2a21;
            border-radius: 16px;
            padding: 5px 10px;
            font-size: 12px;
            font-weight: 500;
            line-height: 1.2;
            white-space: nowrap;
            max-width: 100%;
        }
        .bn-badge.match { background: #b85c38; color: #faf6f0; }
        .bn-warn {
            background: #fff4e8;
            border: 1px solid #e2b089;
            border-radius: 12px;
            padding: 0.7rem 0.85rem;
            margin: 0.55rem 0 0.9rem;
        }
        .bn-info {
            background: #f4ebd9;
            border: 1px solid #b85c38;
            border-left: 5px solid #b85c38;
            border-radius: 12px;
            padding: 0.85rem 1rem;
            margin: 0.55rem 0 0.9rem;
            color: #3c2a21;
        }
        .stButton button, .stDownloadButton button,
        button[data-testid="stBaseButton-primary"] {
            background: #b85c38;
            color: #faf6f0;
            border: 0;
            border-radius: 8px;
            font-weight: 600;
            min-height: 44px;
            padding: 0.45rem 1rem;
            box-shadow: none !important;
            outline: none !important;
            touch-action: manipulation;
        }
        .stButton button:hover, .stDownloadButton button:hover,
        button[data-testid="stBaseButton-primary"]:hover {
            background: #9a4b2d;
            color: #fff;
        }
        .stButton button:focus, .stButton button:focus-visible,
        .stDownloadButton button:focus, .stDownloadButton button:focus-visible,
        .stButton button:active {
            outline: none !important;
            box-shadow: none !important;
        }
        button[data-testid="stBaseButton-secondary"] {
            background: transparent;
            border: 1px solid #b85c38;
            color: #b85c38;
            min-height: 34px;
            padding: 0.3rem 0.85rem;
            font-size: 13px;
            width: auto;
            transition: background 0.18s ease, color 0.18s ease;
        }
        button[data-testid="stBaseButton-secondary"]:hover {
            background: #b85c38;
            color: #fff;
        }
        [data-testid="stTextInput"] input,
        [data-testid="stNumberInput"] input,
        [data-testid="stTextArea"] textarea,
        [data-testid="stSelectbox"] div[data-baseweb="select"] > div,
        [data-testid="stFileUploader"] section {
            border-radius: 8px !important;
            border: 1px solid #e0d6cc !important;
            background: #fff !important;
            box-shadow: none !important;
        }
        [data-testid="stTextInput"] input {
            min-height: 42px;
            padding: 0.5rem 0.85rem;
        }
        [data-testid="stTextInput"] input::placeholder,
        [data-testid="stTextArea"] textarea::placeholder {
            color: #8c7a6b !important;
            opacity: 0.72;
        }
        [data-testid="stTextInput"] input:focus,
        [data-testid="stTextArea"] textarea:focus {
            border-color: #b85c38 !important;
            box-shadow: 0 0 0 2px rgba(184, 92, 56, 0.12) !important;
        }
        .bn-search-dock { height: 0; margin: 0; }
        [data-testid="stSlider"] { padding-top: 0.3rem; padding-bottom: 0.45rem; }
        [data-baseweb="slider"] { touch-action: manipulation; }
        div[data-testid="stButtonGroup"] {
            background: #efe6dc;
            border-radius: 10px;
            padding: 3px;
            gap: 2px;
            width: 100%;
        }
        div[data-testid="stButtonGroup"] button {
            flex: 1 1 0;
            background: transparent !important;
            color: #8c7a6b !important;
            border: 0 !important;
            border-radius: 8px !important;
            box-shadow: none !important;
            min-height: 36px;
            font-size: 13px;
            font-weight: 600;
            padding: 0.35rem 0.5rem;
            transition: background 0.18s ease, color 0.18s ease;
        }
        div[data-testid="stButtonGroup"] button[kind="segmented_controlActive"],
        div[data-testid="stButtonGroup"] button[data-testid="stBaseButton-segmented_controlActive"],
        div[data-testid="stButtonGroup"] button[aria-pressed="true"],
        div[data-testid="stButtonGroup"] button[data-active="true"] {
            background: #b85c38 !important;
            color: #fff !important;
        }
        div[data-testid="stRadio"] > label { display: none; }
        div[data-testid="stRadio"] [role="radiogroup"] {
            background: #efe6dc;
            border-radius: 10px;
            padding: 3px;
            gap: 2px;
            flex-wrap: nowrap;
        }
        div[data-testid="stRadio"] [role="radiogroup"] label {
            background: transparent;
            border: 0;
            border-radius: 8px;
            padding: 0.4rem 0.7rem;
            min-height: 36px;
            font-weight: 600;
            font-size: 13px;
            color: #8c7a6b;
            flex: 1 1 0;
            justify-content: center;
        }
        div[data-testid="stRadio"] [role="radiogroup"] label:has(input:checked) {
            background: #b85c38;
            color: #fff;
        }
        @media (max-width: 768px) {
            .block-container {
                padding-left: max(14px, env(safe-area-inset-left));
                padding-right: max(14px, env(safe-area-inset-right));
                padding-bottom: max(3.75rem, env(safe-area-inset-bottom));
                max-width: 100%;
                overflow-x: hidden;
            }
            [data-testid="stVerticalBlock"],
            [data-testid="stHorizontalBlock"] {
                max-width: 100%;
            }
            [data-testid="stHorizontalBlock"] {
                gap: 0.55rem;
                flex-wrap: wrap;
            }
            .bn-hero { padding: 0.8rem 0.95rem; border-radius: 12px; }
            .bn-hero h1 { font-size: 1.35rem; }
            .bn-card { width: 100%; }
            .bn-card-fallback,
            .bn-bag-img,
            .bn-card.has-photo img.bn-bag-img { height: 180px !important; }
            div[data-testid="stDialog"] > div,
            div[data-testid="stDialog"] div[role="dialog"],
            [data-testid="stModal"] > div,
            [data-testid="stDialog"] [role="dialog"],
            div[role="dialog"] {
                width: min(96vw, 100%) !important;
                max-width: 96vw !important;
                padding: 16px 14px 20px !important;
                margin: 8px !important;
                border-radius: 14px !important;
            }
            .bn-modal-hero,
            .bn-modal-img,
            [data-testid="stDialog"] img {
                max-height: min(220px, 28vh) !important;
            }
            [data-testid="stPlotlyChart"] {
                min-height: 360px;
                padding: 0.35rem 0.15rem 0.5rem;
            }
            .stButton button, .stDownloadButton button,
            button[data-testid="stBaseButton-primary"] { width: 100%; min-height: 48px; }
            button[data-testid="stBaseButton-secondary"] { width: auto; min-height: 44px; }
            [data-testid="stSlider"] { padding-bottom: 0.95rem; }
            [data-testid="stColumn"],
            [data-testid="column"] { width: 100% !important; flex: 1 1 100% !important; }
            div[data-testid="stButtonGroup"] {
                flex-wrap: wrap;
                padding: 4px;
                gap: 4px;
            }
            div[data-testid="stButtonGroup"] button {
                min-height: 48px;
                font-size: 14px;
                padding: 0.55rem 0.4rem;
                white-space: normal;
                line-height: 1.2;
            }
            div[data-testid="stRadio"] [role="radiogroup"] label {
                min-height: 48px;
                font-size: 14px;
            }
            [data-testid="stSelectbox"] div[data-baseweb="select"] > div,
            [data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
                min-height: 48px !important;
                font-size: 16px !important;
            }
            [data-testid="stTextInput"] input,
            [data-testid="stNumberInput"] input,
            [data-testid="stTextArea"] textarea {
                font-size: 16px !important;
                min-height: 48px;
            }
            [data-testid="stFileUploader"] section,
            [data-testid="stFileUploaderDropzone"] {
                min-height: 120px !important;
            }
            [data-testid="stFileUploader"] button,
            [data-testid="stFileUploaderDropzone"] button {
                width: 100% !important;
                min-height: 52px !important;
                font-size: 16px !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def stars(value: float | None) -> str:
    if not value:
        return "★ —"
    return f"★ {value:.1f}"


def flavor_radar(
    user: dict | None,
    community: dict,
    labels: list[str],
    you: str,
    community_label: str,
) -> go.Figure:
    axes = labels
    comm = [
        community.get("acidity") or 0,
        community.get("sweetness") or 0,
        community.get("body") or 0,
        community.get("aftertaste") or 0,
    ]
    fig = go.Figure()
    fig.add_trace(
        go.Scatterpolar(
            r=comm + comm[:1],
            theta=axes + axes[:1],
            fill="toself",
            name=community_label,
            line=dict(color="#3c2a21", width=2),
            fillcolor="rgba(60, 42, 33, 0.18)",
        )
    )
    if user:
        mine = [
            user.get("acidity") or 0,
            user.get("sweetness") or 0,
            user.get("body") or 0,
            user.get("aftertaste") or 0,
        ]
        fig.add_trace(
            go.Scatterpolar(
                r=mine + mine[:1],
                theta=axes + axes[:1],
                fill="toself",
                name=you,
                line=dict(color="#b85c38", width=2),
                fillcolor="rgba(184, 92, 56, 0.28)",
            )
        )
    fig.update_traces(hoverinfo="skip", hovertemplate=None, cliponaxis=False)
    fig.update_layout(
        polar=dict(
            bgcolor="#fffdf9",
            domain=dict(x=[0.16, 0.84], y=[0.18, 0.86]),
            radialaxis=dict(
                visible=True,
                range=[0, 5],
                tickvals=[1, 2, 3, 4, 5],
                tickfont=dict(size=10, color="#8c7a6b"),
                gridcolor="#eae3d9",
                linecolor="#e8d8c8",
            ),
            angularaxis=dict(
                rotation=90,
                direction="clockwise",
                tickfont=dict(size=13, color="#3c2a21", family="Outfit, sans-serif"),
                gridcolor="#eae3d9",
                linecolor="#e8d8c8",
                ticks="",
                layer="above traces",
            ),
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Outfit, sans-serif", color="#3c2a21"),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            x=0.5,
            xanchor="center",
            font=dict(size=12),
            bgcolor="rgba(0,0,0,0)",
        ),
        margin=dict(l=56, r=56, t=48, b=48, pad=8),
        height=400,
        autosize=True,
        dragmode=False,
        hovermode=False,
        uirevision="beannote-radar",
        showlegend=True,
    )
    return fig


def flavor_badges_html(
    tags: list[str] | str | None,
    overlap: list[str] | None = None,
    extra: list[str] | str | None = None,
    limit: int = 8,
) -> str:
    """Official 1–2 word flavor pills only. Never emits sentence fragments."""
    pills = extract_flavor_tags(tags, extra)[:limit]
    if not pills:
        return ""
    matched = set(overlap or [])
    chips = []
    for tag in pills:
        klass = "bn-badge match" if tag in matched else "bn-badge"
        chips.append(f'<span class="{klass}">{escape(tag)}</span>')
    return f'<div class="bn-badges">{"".join(chips)}</div>'


def note_chips(tags: list[str], overlap: list[str]) -> str:
    return flavor_badges_html(tags, overlap=overlap)


def bag_image_markup(photo: Path, kind: str = "card") -> str:
    raw = photo.read_bytes()
    suffix = photo.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    encoded = base64.b64encode(raw).decode("ascii")
    if kind == "modal":
        return (
            f'<div class="bn-modal-hero">'
            f'<img class="bn-modal-img" src="data:{mime};base64,{encoded}" alt="" />'
            f"</div>"
        )
    return f'<img class="bn-bag-img" src="data:{mime};base64,{encoded}" alt="" />'


def bean_card_markup(bean: dict, lang: str) -> str:
    avg = bean.get("avg_rating") or 0
    tags_html = flavor_badges_html(
        bean.get("flavor_tags"), extra=bean.get("roaster_notes"), limit=4
    )
    photo = resolve_image_path(bean.get("image_url") or "")
    media = bag_image_markup(photo) if photo else BAG_PLACEHOLDER
    return (
        f'<div class="bn-card has-photo bn-card-hit">'
        f"{media}"
        f'<div class="bn-card-body">'
        f'<div class="bn-roaster">{escape(str(bean.get("roaster") or ""))}</div>'
        f'<h3 class="bn-name">{escape(str(bean.get("name") or ""))}</h3>'
        f'<div class="bn-stars">{stars(avg)}</div>'
        f'<div class="bn-meta">{escape(str(bean.get("origin") or "—"))} · '
        f'{escape(str(bean.get("process") or "—"))} · '
        f'{escape(str(bean.get("roast_level") or "—"))}</div>'
        f"<div>{tags_html}</div>"
        f'<div class="bn-card-cta">{escape(t(lang, "details"))}</div>'
        f"</div></div>"
    )


def go_review(bean_id: int, notes: str | None = None) -> None:
    st.session_state.selected_bean_id = bean_id
    st.session_state.pending_tab = "review"
    st.session_state.pending_review_bean_id = bean_id
    st.session_state.pop("pending_similar", None)
    st.session_state.pop("force_insert", None)
    if notes:
        st.session_state.pending_review_notes = notes


def tasting_notes_from_form() -> str:
    notes = (st.session_state.get("add_notes") or "").strip()
    if notes:
        return notes
    flavors = st.session_state.get("add_flavors") or []
    return ", ".join(flavors)


def apply_pending_ui() -> None:
    """Apply nav/form resets before their widgets are instantiated this run."""
    pending = st.session_state.pop("pending_tab", None)
    if pending:
        st.session_state.active_tab = pending
    pending_bean = st.session_state.pop("pending_review_bean_id", None)
    if pending_bean is not None:
        st.session_state.selected_bean_id = pending_bean
        st.session_state.review_bean_id = pending_bean
    pending_notes = st.session_state.pop("pending_review_notes", None)
    if pending_notes is not None:
        st.session_state.review_notes = pending_notes
    if not st.session_state.pop("reset_add_form", False):
        return
    st.session_state.ocr_form = {}
    st.session_state.pending_similar = None
    st.session_state.pending_image_url = ""
    st.session_state.add_name = ""
    st.session_state.add_roaster = ""
    st.session_state.add_origin = ""
    st.session_state.add_process = ""
    st.session_state.add_roast = ""
    st.session_state.add_notes = ""
    st.session_state.add_flavors = []
    st.session_state.add_story = ""


def apply_ocr_form(parsed: dict) -> None:
    """Write Gemini/OCR fields into widget keys so inputs update on the next rerun."""
    parsed = normalize_scan_fields(parsed)
    flavors = [tag for tag in (parsed.get("flavor_notes") or []) if tag in FLAVOR_NOTES]
    process = parsed.get("process") or ""
    roast = parsed.get("roast_level") or ""
    if process not in PROCESSES:
        process = ""
    if roast not in ROAST_LEVELS:
        roast = ""
    st.session_state.ocr_form = parsed
    st.session_state.pending_similar = parsed.get("similar") or []
    st.session_state.add_name = parsed.get("name") or ""
    st.session_state.add_roaster = parsed.get("roaster") or ""
    st.session_state.add_origin = parsed.get("origin") or ""
    st.session_state.add_process = process
    st.session_state.add_roast = roast
    st.session_state.add_notes = parsed.get("official_notes") or parsed.get("roaster_notes") or ""
    st.session_state.add_flavors = flavors
    st.session_state.add_story = parsed.get("story") or ""
    if parsed.get("image_url"):
        st.session_state.pending_image_url = parsed["image_url"]
    filled = bool((parsed.get("name") or "").strip() or (parsed.get("roaster") or "").strip())
    st.session_state.ocr_flash = "scanned" if filled else "ocr_empty"


def clear_ocr_form() -> None:
    st.session_state.ocr_form = {}
    st.session_state.pending_similar = None
    st.session_state.pending_image_url = ""
    st.session_state.reset_add_form = True


def pending_image_url() -> str:
    return (st.session_state.get("pending_image_url") or "").strip()


def process_scan_image(image_file) -> None:
    """Gemini/OCR + fuzzy match a snapped/uploaded bag, then route to Rate or Add."""
    from ocr import scan_available, scan_label

    raw = image_file.getvalue()
    filename = getattr(image_file, "name", "") or "scan.jpg"
    try:
        image_bytes = encode_scan_jpeg(raw)
        filename = "scan.jpg"
    except Exception:
        image_bytes = raw
    image_url = save_bean_image(image_bytes, filename)

    if not scan_available():
        st.session_state.pending_image_url = image_url
        st.session_state.ocr_flash = "ocr_missing"
        st.session_state.pending_tab = "add"
        st.session_state.scan_panel_open = False
        return

    try:
        parsed = scan_label(image_bytes)
    except Exception:
        st.session_state.pending_image_url = image_url
        st.session_state.ocr_flash = "ocr_fail"
        st.session_state.pending_tab = "add"
        st.session_state.scan_panel_open = False
        return

    parsed["image_url"] = image_url
    st.session_state.pending_image_url = image_url
    st.session_state.scan_panel_open = False

    match = parsed.get("scan_match")
    if parsed.get("scan_action") == "rate" and match:
        if not (match.get("image_url") or "").strip():
            update_bean_image(match["id"], image_url)
        story = (parsed.get("story") or "").strip()
        if story and not (match.get("story") or "").strip():
            update_bean_story(match["id"], story)
        notes = parsed.get("roaster_notes") or ", ".join(parsed.get("flavor_notes") or [])
        st.session_state.bean_found_flash = True
        go_review(match["id"], notes=notes)
        return

    apply_ocr_form(parsed)
    st.session_state.pending_tab = "add"


def render_scan_cta(lang: str) -> None:
    st.markdown(
        f'<div class="bn-scan-card">'
        f'<div class="bn-scan-title">{escape(t(lang, "scan_card_title"))}</div>'
        f'<p class="bn-scan-sub">{escape(t(lang, "scan_card_sub"))}</p>'
        f"</div>",
        unsafe_allow_html=True,
    )
    uploaded = st.file_uploader(
        t(lang, "scan_pick_image"),
        type=SCAN_IMAGE_TYPES,
        key="scan_upload",
        label_visibility="collapsed",
        accept_multiple_files=False,
    )
    if not uploaded:
        return
    digest = hashlib.md5(uploaded.getvalue()).hexdigest()
    if st.session_state.get("last_scan_digest") == digest:
        return
    st.session_state.last_scan_digest = digest
    process_scan_image(uploaded)
    st.rerun()


def render_exact_match(lang: str, similar: list[dict]) -> None:
    top = similar[0]
    pct = int(round((top.get("confidence") or 1) * 100))
    st.markdown(
        f'<div class="bn-info"><strong>{t(lang, "exact_match_banner").format(pct=pct)}</strong></div>',
        unsafe_allow_html=True,
    )
    st.markdown(f"**{top['name']}** · {top['roaster']}")
    if st.button(t(lang, "go_rate_existing"), type="primary", use_container_width=True):
        go_review(top["id"], notes=tasting_notes_from_form())
        st.rerun()


def render_duplicate_warning(lang: str, similar: list[dict]) -> None:
    if not similar:
        return
    st.markdown(
        f'<div class="bn-warn"><strong>{t(lang, "duplicate_warning")}</strong>'
        f'<br>{t(lang, "near_match_hint")}</div>',
        unsafe_allow_html=True,
    )
    notes = tasting_notes_from_form()
    for match in similar[:3]:
        cols = st.columns([3, 2])
        pct = int(match.get("confidence", 0) * 100)
        cols[0].markdown(
            f"**{match['name']}** · {match['roaster']}  \n"
            f"{pct}% {t(lang, 'confidence')}"
        )
        if cols[1].button(
            t(lang, "use_existing"),
            key=f"use-{match['id']}-{match.get('confidence')}",
            type="secondary",
        ):
            go_review(match["id"], notes=notes)
            st.rerun()


def sidebar(lang: str) -> dict:
    with st.sidebar:
        st.markdown("### ☕ BeanNote")
        st.caption(t(lang, "tagline"))
        picked = st.selectbox(
            t(lang, "language"),
            list(LANGS.keys()),
            format_func=lambda k: LANGS[k],
            index=list(LANGS.keys()).index(lang) if lang in LANGS else 0,
            key="lang",
        )
        st.markdown(f"**{t(lang, 'filters')}**")
        origin = st.selectbox(
            t(lang, "origin"),
            [""] + distinct_values("origin"),
            format_func=lambda v: t(lang, "all") if v == "" else v,
        )
        roast = st.selectbox(
            t(lang, "roast"),
            [""] + ROAST_FILTERS,
            format_func=lambda v: t(lang, "all") if v == "" else v,
        )
        min_rating = st.slider(t(lang, "min_rating"), 0.0, 5.0, 0.0, 0.5)
        st.divider()
        st.caption(t(lang, "export_help"))
        csv_name, csv_mime, csv_bytes = export_ratings("csv")
        json_name, json_mime, json_bytes = export_ratings("json")
        c1, c2 = st.columns(2)
        c1.download_button(t(lang, "export_csv"), csv_bytes, csv_name, csv_mime, use_container_width=True)
        c2.download_button(t(lang, "export_json"), json_bytes, json_name, json_mime, use_container_width=True)
        st.caption(f"{t(lang, 'version')} {VERSION} · {os.getenv('ENVIRONMENT', 'local')}")
    return {"lang": picked, "origin": origin, "roast": roast, "min_rating": min_rating}


@st.dialog("BeanNote", width="large")
def bean_dialog(bean_id: int, lang: str) -> None:
    profile = get_flavor_profile(bean_id)
    bean = profile["bean"]
    community = profile["community"]
    user = profile["user"]
    photo = resolve_image_path(bean.get("image_url") or "")
    if photo:
        st.markdown(bag_image_markup(photo, kind="modal"), unsafe_allow_html=True)
    st.markdown(f'<div class="bn-roaster">{escape(str(bean["roaster"]))}</div>', unsafe_allow_html=True)
    st.markdown(f'<h3 class="bn-name">{escape(str(bean["name"]))}</h3>', unsafe_allow_html=True)
    st.caption(f"{bean['origin']} · {bean['process']} · {bean['roast_level']}")
    avg = community.get("avg_rating") or 0
    st.markdown(
        f'<div class="bn-stars">{stars(avg)}</div>'
        f'<div class="bn-meta">{community["rating_count"]} {t(lang, "reviews")}</div>',
        unsafe_allow_html=True,
    )
    tags_html = flavor_badges_html(bean.get("flavor_tags"), extra=bean.get("roaster_notes"))
    if tags_html:
        st.markdown(tags_html, unsafe_allow_html=True)
    render_bean_story(lang, bean.get("story") or "")
    labels = [t(lang, k) for k in ("acidity", "sweetness", "body", "aftertaste")]
    st.plotly_chart(
        flavor_radar(user, community, labels, t(lang, "radar_you"), t(lang, "radar_community")),
        use_container_width=True,
        config=RADAR_CHART_CONFIG,
    )
    compare_notes(
        lang,
        bean.get("roaster_notes") or "",
        (user or {}).get("notes") or "",
        bean.get("flavor_tags") or [],
    )
    if st.button(t(lang, "tab_review"), type="primary", use_container_width=True):
        go_review(bean_id)
        st.rerun()


def render_bean_story(lang: str, story: str) -> None:
    text = (story or "").strip()
    if not text:
        return
    st.markdown(
        f'<div class="bn-story">'
        f'<div class="bn-story-kicker">📖 {escape(t(lang, "bean_story"))}</div>'
        f"<p>{escape(text)}</p>"
        f"</div>",
        unsafe_allow_html=True,
    )


def compare_notes(
    lang: str,
    roaster_notes: str,
    user_notes: str,
    flavor_tags: list[str] | None = None,
) -> None:
    match = compare_flavor_notes(roaster_notes, user_notes, flavor_tags)
    left, right = st.columns(2)
    left.markdown(f"**{t(lang, 'roaster_notes')}**")
    left.markdown(note_chips(match["roaster"], match["overlap"]) or "—", unsafe_allow_html=True)
    right.markdown(f"**{t(lang, 'user_notes')}**")
    right.markdown(note_chips(match["user"], match["overlap"]) or "—", unsafe_allow_html=True)
    if match["overlap"]:
        pills = flavor_badges_html(match["overlap"], overlap=match["overlap"])
        st.markdown(
            f'<div class="bn-match-box"><div class="bn-meta">{escape(t(lang, "matching_notes"))}</div>{pills}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.caption(t(lang, "no_match_notes"))


def render_explore(lang: str, filters: dict, query: str = "") -> None:
    beans = list_beans(
        search=query,
        origin=filters["origin"],
        roast_level=filters["roast"],
        min_rating=filters["min_rating"],
    )
    if not beans:
        st.info(t(lang, "no_beans") if query or filters["origin"] or filters["roast"] or filters["min_rating"] else t(lang, "empty_explore"))
        return

    for start in range(0, len(beans), 2):
        cols = st.columns(2)
        for col, bean in zip(cols, beans[start : start + 2]):
            with col:
                st.markdown(bean_card_markup(bean, lang), unsafe_allow_html=True)
                if st.button(t(lang, "details"), key=f"open-{bean['id']}", type="secondary"):
                    st.session_state.detail_bean_id = bean["id"]
                    bean_dialog(bean["id"], lang)


def render_review(lang: str) -> None:
    beans = list_beans()
    if not beans:
        st.info(t(lang, "empty_explore"))
        return

    options = {b["id"]: f"{b['name']} — {b['roaster']}" for b in beans}
    ids = list(options.keys())
    default_id = st.session_state.get("selected_bean_id") or beans[0]["id"]
    if st.session_state.get("review_bean_id") not in ids:
        st.session_state.review_bean_id = default_id if default_id in ids else ids[0]
    bean_id = st.selectbox(
        t(lang, "select_bean"),
        ids,
        format_func=lambda i: options[i],
        key="review_bean_id",
    )
    st.session_state.selected_bean_id = bean_id

    profile = get_flavor_profile(bean_id)
    bean = profile["bean"]
    community = profile["community"]
    latest = profile["user"]

    if st.session_state.pop("bean_found_flash", False):
        st.toast(t(lang, "bean_found"), icon="☕")
        st.markdown(
            f'<div class="bn-info"><strong>{t(lang, "bean_found")}</strong></div>',
            unsafe_allow_html=True,
        )

    photo = resolve_image_path(bean.get("image_url") or "")
    if photo:
        st.image(str(photo), width=220)
    st.caption(f"{bean['origin']} · {bean['process']} · {bean['roast_level']}")
    brew = st.selectbox(t(lang, "brew_method"), BREW_METHODS)
    rating = st.slider(t(lang, "rating"), min_value=1.0, max_value=5.0, value=4.0, step=0.5, help=t(lang, "help_rating"))
    c1, c2 = st.columns(2)
    acidity = c1.slider(t(lang, "acidity"), min_value=1.0, max_value=5.0, value=3.5, step=0.5, help=t(lang, "help_acidity"))
    sweetness = c2.slider(t(lang, "sweetness"), min_value=1.0, max_value=5.0, value=3.5, step=0.5, help=t(lang, "help_sweetness"))
    body = c1.slider(t(lang, "body"), min_value=1.0, max_value=5.0, value=3.5, step=0.5, help=t(lang, "help_body"))
    aftertaste = c2.slider(t(lang, "aftertaste"), min_value=1.0, max_value=5.0, value=3.5, step=0.5, help=t(lang, "help_aftertaste"))
    notes = st.text_area(
        t(lang, "notes"),
        placeholder=t(lang, "notes_ph"),
        height=100,
        key="review_notes",
    )

    live_user = {
        "acidity": acidity,
        "sweetness": sweetness,
        "body": body,
        "aftertaste": aftertaste,
        "notes": notes,
    }
    labels = [t(lang, k) for k in ("acidity", "sweetness", "body", "aftertaste")]
    st.plotly_chart(
        flavor_radar(live_user, community, labels, t(lang, "radar_you"), t(lang, "radar_community")),
        use_container_width=True,
        config=RADAR_CHART_CONFIG,
    )
    compare_notes(
        lang,
        bean.get("roaster_notes") or "",
        notes or ((latest or {}).get("notes") or ""),
        bean.get("flavor_tags") or [],
    )

    if st.button(t(lang, "save_rating"), type="primary", use_container_width=True):
        insert_rating(bean_id, brew, rating, acidity, sweetness, body, aftertaste, notes)
        st.toast(t(lang, "saved_toast"), icon="☕")
        st.success(t(lang, "saved_toast"))


def render_add(lang: str) -> None:
    from ocr import scan_available, scan_label

    uploaded = st.file_uploader(
        t(lang, "upload"),
        type=SCAN_IMAGE_TYPES,
        help=t(lang, "upload_help"),
        accept_multiple_files=False,
    )
    if uploaded and st.button(t(lang, "scan"), type="primary", use_container_width=True):
        if not scan_available():
            st.error(t(lang, "ocr_missing"))
        else:
            try:
                raw = uploaded.getvalue()
                try:
                    image_bytes = encode_scan_jpeg(raw)
                    filename = "scan.jpg"
                except Exception:
                    image_bytes = raw
                    filename = uploaded.name
                parsed = scan_label(image_bytes)
                parsed["image_url"] = save_bean_image(image_bytes, filename)
                apply_ocr_form(parsed)
                st.rerun()
            except Exception:
                st.error(t(lang, "ocr_fail"))

    flash = st.session_state.pop("ocr_flash", None)
    if flash == "scanned":
        st.success(t(lang, "scanned"))
    elif flash == "ocr_empty":
        st.warning(t(lang, "ocr_empty"))
    elif flash == "ocr_missing":
        st.error(t(lang, "ocr_missing"))
    elif flash == "ocr_fail":
        st.error(t(lang, "ocr_fail"))

    attached = resolve_image_path(pending_image_url())
    if attached:
        st.caption(t(lang, "attached_label"))
        st.image(str(attached), width=180)

    form = st.session_state.get("ocr_form") or {}
    name = st.text_input(t(lang, "add_name"), key="add_name")
    roaster = st.text_input(t(lang, "add_roaster"), key="add_roaster")
    c1, c2, c3 = st.columns(3)
    origin = c1.text_input(t(lang, "origin"), key="add_origin")
    process = c2.selectbox(t(lang, "add_process"), [""] + PROCESSES, key="add_process")
    roast = c3.selectbox(t(lang, "add_roast"), [""] + ROAST_LEVELS, key="add_roast")
    flavors = st.multiselect(t(lang, "add_flavor_notes"), FLAVOR_NOTES, key="add_flavors")
    roaster_notes = st.text_area(t(lang, "add_roaster_notes"), key="add_notes", height=90)
    st.markdown(
        f'<div class="bn-story-head">📖 {escape(t(lang, "bean_story"))}</div>',
        unsafe_allow_html=True,
    )
    story = st.text_area(
        t(lang, "bean_story"),
        key="add_story",
        height=130,
        help=t(lang, "bean_story_help"),
        placeholder=t(lang, "bean_story_ph"),
        label_visibility="collapsed",
    )
    if "raw_text" in form:
        with st.expander(t(lang, "raw_ocr")):
            st.code(form.get("raw_text") or "—")

    if name.strip():
        similar = find_similar_beans(name, roaster)
    else:
        similar = st.session_state.get("pending_similar") or []
    st.session_state.pending_similar = similar
    tier = classify_matches(similar)

    if tier == "exact":
        render_exact_match(lang, similar)
        return
    if tier == "near":
        render_duplicate_warning(lang, similar)

    save_new = False
    force = False
    if tier == "near":
        force = st.button(t(lang, "save_as_new"), use_container_width=True)
    else:
        save_new = st.button(t(lang, "save_bean"), type="primary", use_container_width=True)

    if save_new or force:
        if not name.strip() or not roaster.strip():
            st.error(t(lang, "required"))
            return
        result = insert_bean(
            name=name,
            roaster=roaster,
            origin=origin,
            process=process,
            roast_level=roast,
            roaster_notes=roaster_notes or ", ".join(flavors),
            flavor_tags=extract_flavor_tags(flavors) or None,
            skip_fuzzy=force,
            image_url=pending_image_url() or (form.get("image_url") or ""),
            story=story,
        )
        if result["status"] in {"fuzzy", "exact"}:
            st.session_state.pending_similar = result["similar"]
            st.rerun()
        if result["status"] == "exists":
            st.warning(t(lang, "exists"))
            go_review(result["bean"]["id"], notes=tasting_notes_from_form())
            st.rerun()
        if result["status"] == "created":
            clear_ocr_form()
            st.toast(t(lang, "created"), icon="☕")
            go_review(result["bean"]["id"], notes=tasting_notes_from_form())
            st.rerun()


def main() -> None:
    init_db()
    apply_pending_ui()
    inject_css()
    lang = st.session_state.get("lang", "da")
    filters = sidebar(lang)
    lang = filters["lang"]

    st.markdown(
        f'<div class="bn-hero">'
        f'<div class="bn-kicker">☕ {t(lang, "app_name")}</div>'
        f'<h1>{t(lang, "app_name")}</h1>'
        f'<p>{t(lang, "tagline")}</p>'
        f"</div>",
        unsafe_allow_html=True,
    )
    render_scan_cta(lang)

    tab_keys = ["explore", "review", "add"]
    tab_labels = {
        "explore": f"☕ {t(lang, 'tab_explore')}",
        "review": f"✍️ {t(lang, 'tab_review')}",
        "add": f"➕ {t(lang, 'tab_add')}",
    }
    if st.session_state.get("active_tab") not in tab_keys:
        st.session_state.active_tab = "explore"
    current = st.segmented_control(
        "nav",
        options=tab_keys,
        format_func=lambda key: tab_labels[key],
        key="active_tab",
        label_visibility="collapsed",
        width="stretch",
    ) or "explore"
    if current == "explore":
        st.markdown('<div class="bn-search-dock"></div>', unsafe_allow_html=True)
        query = st.text_input(
            t(lang, "search"),
            placeholder=t(lang, "search"),
            label_visibility="collapsed",
        )
        render_explore(lang, filters, query)
    elif current == "review":
        render_review(lang)
    else:
        render_add(lang)


if __name__ == "__main__":
    main()
