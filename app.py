"""BeanNote — personal coffee journal. Streamlit UI."""

from __future__ import annotations

import os

import plotly.graph_objects as go
import streamlit as st

from db import (
    VERSION,
    distinct_values,
    export_ratings,
    find_similar_beans,
    get_flavor_profile,
    init_db,
    insert_bean,
    insert_rating,
    list_beans,
    matching_flavor_tags,
)
from translations import LANGS, t

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
ROAST_LEVELS = ["Light", "Medium", "Medium-Dark", "Dark"]
PROCESSES = ["Washed", "Natural", "Honey", "Anaerobic"]

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
        <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Outfit:wght@400;500;600&display=swap" rel="stylesheet">
        <style>
        :root {
            --espresso: #3c2a21;
            --terracotta: #b85c38;
            --cream: #faf6f0;
            --foam: #f3ebe3;
            --latte: #e8d8c8;
        }
        html, body, [data-testid="stAppViewContainer"] {
            background: var(--cream);
            color: var(--espresso);
            font-family: "Outfit", "Avenir Next", sans-serif;
        }
        [data-testid="stHeader"] { background: transparent; }
        #MainMenu, footer, [data-testid="stToolbar"] { visibility: hidden; }
        .block-container { padding-top: 1.2rem; padding-bottom: 4rem; max-width: 1100px; }
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
            border-radius: 12px;
            min-height: 44px;
        }
        .bn-hero {
            background: linear-gradient(135deg, #3c2a21 0%, #5a3c2e 58%, #b85c38 140%);
            color: #faf6f0;
            border-radius: 22px;
            padding: 1.4rem 1.6rem 1.3rem;
            margin-bottom: 1.1rem;
            box-shadow: 0 14px 40px rgba(60, 42, 33, 0.18);
        }
        .bn-hero h1 { color: #faf6f0; margin: 0 0 0.2rem; font-size: 2.1rem; }
        .bn-hero p { margin: 0; opacity: 0.88; font-size: 1.02rem; }
        .bn-card {
            background: #fffdf9;
            border: 1px solid #eadfd3;
            border-radius: 18px;
            padding: 1rem 1.05rem 0.85rem;
            box-shadow: 0 8px 24px rgba(60, 42, 33, 0.06);
            min-height: 210px;
        }
        .bn-card h3 { margin: 0.15rem 0 0.1rem; font-size: 1.25rem; }
        .bn-meta { color: #7a5a48; font-size: 0.92rem; margin-bottom: 0.45rem; }
        .bn-stars { color: #b85c38; font-size: 1.05rem; letter-spacing: 0.04em; }
        .bn-badge {
            display: inline-block;
            background: #f3ebe3;
            color: #3c2a21;
            border-radius: 999px;
            padding: 0.18rem 0.62rem;
            margin: 0.12rem 0.22rem 0.12rem 0;
            font-size: 0.78rem;
            font-weight: 500;
        }
        .bn-badge.match { background: #b85c38; color: #faf6f0; }
        .bn-warn {
            background: #fff4e8;
            border: 1px solid #e2b089;
            border-radius: 14px;
            padding: 0.85rem 1rem;
            margin: 0.6rem 0 1rem;
        }
        .stButton button, .stDownloadButton button {
            background: #b85c38;
            color: #faf6f0;
            border: 0;
            border-radius: 12px;
            font-weight: 600;
            min-height: 46px;
            padding: 0.55rem 1.1rem;
        }
        .stButton button:hover { background: #9a4b2d; color: #fff; }
        [data-testid="stSlider"] { padding-top: 0.35rem; padding-bottom: 0.55rem; }
        [data-baseweb="slider"] { touch-action: manipulation; }
        div[data-testid="stTabs"] button { font-weight: 600; }
        div[data-testid="stRadio"] > label { display: none; }
        div[data-testid="stRadio"] [role="radiogroup"] {
            gap: 0.4rem;
            flex-wrap: wrap;
        }
        div[data-testid="stRadio"] [role="radiogroup"] label {
            background: #fffdf9;
            border: 1px solid #eadfd3;
            border-radius: 12px;
            padding: 0.45rem 0.85rem;
            min-height: 44px;
            font-weight: 600;
        }
        @media (max-width: 768px) {
            .block-container { padding-left: 0.7rem; padding-right: 0.7rem; }
            .bn-hero { padding: 1.1rem 1rem; border-radius: 16px; }
            .bn-hero h1 { font-size: 1.65rem; }
            .stButton button, .stDownloadButton button { width: 100%; min-height: 48px; }
            [data-testid="stSlider"] { padding-bottom: 0.9rem; }
            [data-testid="column"] { width: 100% !important; flex: 1 1 100% !important; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def stars(value: float | None) -> str:
    if not value:
        return "☆☆☆☆☆"
    filled = max(0, min(5, int(round(value))))
    return "★" * filled + "☆" * (5 - filled)


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
    fig.update_layout(
        polar=dict(
            bgcolor="#fffdf9",
            radialaxis=dict(visible=True, range=[0, 5], tickfont=dict(size=11)),
            angularaxis=dict(tickfont=dict(size=13, color="#3c2a21")),
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=-0.18),
        margin=dict(l=30, r=30, t=20, b=40),
        height=340,
    )
    return fig


def note_chips(tags: list[str], overlap: list[str]) -> str:
    html = []
    for tag in tags:
        klass = "bn-badge match" if any(tag == o or tag in o or o in tag for o in overlap) else "bn-badge"
        html.append(f'<span class="{klass}">{tag}</span>')
    return "".join(html) or '<span class="bn-badge">—</span>'


def go_review(bean_id: int) -> None:
    st.session_state.selected_bean_id = bean_id
    st.session_state.active_tab = "review"
    st.session_state.pop("pending_similar", None)
    st.session_state.pop("force_insert", None)


def render_duplicate_warning(lang: str, similar: list[dict]) -> None:
    if not similar:
        return
    st.markdown(
        f'<div class="bn-warn"><strong>{t(lang, "duplicate_warning")}</strong></div>',
        unsafe_allow_html=True,
    )
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
            use_container_width=True,
        ):
            go_review(match["id"])
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
            [""] + ROAST_LEVELS,
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


@st.dialog("BeanNote")
def bean_dialog(bean_id: int, lang: str) -> None:
    profile = get_flavor_profile(bean_id)
    bean = profile["bean"]
    community = profile["community"]
    user = profile["user"]
    st.markdown(f"### {bean['name']}")
    st.caption(f"{bean['roaster']} · {bean['origin']} · {bean['process']} · {bean['roast_level']}")
    avg = community.get("avg_rating") or 0
    st.markdown(
        f'<div class="bn-stars">{stars(avg)} · {avg:.1f} · {community["rating_count"]} {t(lang, "reviews")}</div>',
        unsafe_allow_html=True,
    )
    tags = bean.get("flavor_tags") or []
    st.markdown("".join(f'<span class="bn-badge">{tag}</span>' for tag in tags), unsafe_allow_html=True)
    labels = [t(lang, k) for k in ("acidity", "sweetness", "body", "aftertaste")]
    st.plotly_chart(
        flavor_radar(user, community, labels, t(lang, "radar_you"), t(lang, "radar_community")),
        use_container_width=True,
    )
    compare_notes(lang, bean.get("roaster_notes") or "", (user or {}).get("notes") or "")
    if st.button(t(lang, "tab_review"), use_container_width=True):
        go_review(bean_id)
        st.rerun()


def compare_notes(lang: str, roaster_notes: str, user_notes: str) -> None:
    match = matching_flavor_tags(roaster_notes, user_notes)
    left, right = st.columns(2)
    left.markdown(f"**{t(lang, 'roaster_notes')}**")
    left.write(roaster_notes or "—")
    left.markdown(note_chips(match["roaster"], match["overlap"]), unsafe_allow_html=True)
    right.markdown(f"**{t(lang, 'user_notes')}**")
    right.write(user_notes or "—")
    right.markdown(note_chips(match["user"], match["overlap"]), unsafe_allow_html=True)
    if match["overlap"]:
        st.success(f"{t(lang, 'matching_notes')}: {', '.join(match['overlap'])}")
    else:
        st.caption(t(lang, "no_match_notes"))


def render_explore(lang: str, filters: dict) -> None:
    query = st.text_input(t(lang, "search"), placeholder=t(lang, "search"))
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
                avg = bean.get("avg_rating") or 0
                tags = "".join(
                    f'<span class="bn-badge">{tag}</span>'
                    for tag in (bean.get("flavor_tags") or [])[:4]
                )
                st.markdown(
                    f"""
                    <div class="bn-card">
                        <div class="bn-meta">{bean['roaster']}</div>
                        <h3>{bean['name']}</h3>
                        <div class="bn-stars">{stars(avg)} {f'{avg:.1f}' if avg else '—'}</div>
                        <div class="bn-meta">{bean['origin']} · {bean['process'] or '—'} · {bean['roast_level'] or '—'}</div>
                        <div>{tags}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if st.button(t(lang, "details"), key=f"open-{bean['id']}", use_container_width=True):
                    st.session_state.detail_bean_id = bean["id"]
                    bean_dialog(bean["id"], lang)


def render_review(lang: str) -> None:
    beans = list_beans()
    if not beans:
        st.info(t(lang, "empty_explore"))
        return

    options = {b["id"]: f"{b['name']} — {b['roaster']}" for b in beans}
    default_id = st.session_state.get("selected_bean_id") or beans[0]["id"]
    ids = list(options.keys())
    index = ids.index(default_id) if default_id in ids else 0
    bean_id = st.selectbox(t(lang, "select_bean"), ids, index=index, format_func=lambda i: options[i])
    st.session_state.selected_bean_id = bean_id

    profile = get_flavor_profile(bean_id)
    bean = profile["bean"]
    community = profile["community"]
    latest = profile["user"]

    st.caption(f"{bean['origin']} · {bean['process']} · {bean['roast_level']}")
    brew = st.selectbox(t(lang, "brew_method"), BREW_METHODS)
    rating = st.slider(t(lang, "rating"), 1.0, 5.0, 4.0, 0.1)
    c1, c2 = st.columns(2)
    acidity = c1.slider(t(lang, "acidity"), 1.0, 5.0, 3.5, 0.1)
    sweetness = c2.slider(t(lang, "sweetness"), 1.0, 5.0, 3.5, 0.1)
    body = c1.slider(t(lang, "body"), 1.0, 5.0, 3.5, 0.1)
    aftertaste = c2.slider(t(lang, "aftertaste"), 1.0, 5.0, 3.5, 0.1)
    notes = st.text_area(t(lang, "notes"), placeholder=t(lang, "notes_ph"), height=100)

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
    )
    compare_notes(lang, bean.get("roaster_notes") or "", notes or ((latest or {}).get("notes") or ""))

    if st.button(t(lang, "save_rating"), type="primary", use_container_width=True):
        insert_rating(bean_id, brew, rating, acidity, sweetness, body, aftertaste, notes)
        st.toast(t(lang, "saved_toast"), icon="☕")
        st.success(t(lang, "saved_toast"))


def render_add(lang: str) -> None:
    from ocr import configure_tesseract, scan_label

    uploaded = st.file_uploader(
        t(lang, "upload"),
        type=["jpg", "jpeg", "png"],
        help=t(lang, "upload_help"),
    )
    if uploaded and st.button(t(lang, "scan"), use_container_width=True):
        if not configure_tesseract():
            st.error(t(lang, "ocr_missing"))
        else:
            try:
                parsed = scan_label(uploaded.getvalue())
                st.session_state.ocr_form = parsed
                st.session_state.pending_similar = parsed.get("similar") or []
                st.success(t(lang, "scanned"))
            except Exception:
                st.error(t(lang, "ocr_fail"))

    form = st.session_state.get("ocr_form") or {}
    name = st.text_input(t(lang, "add_name"), value=form.get("name", ""))
    roaster = st.text_input(t(lang, "add_roaster"), value=form.get("roaster", ""))
    c1, c2, c3 = st.columns(3)
    origin = c1.text_input(t(lang, "origin"), value=form.get("origin", ""))
    process = c2.selectbox(
        t(lang, "add_process"),
        [""] + PROCESSES,
        index=(PROCESSES.index(form["process"]) + 1) if form.get("process") in PROCESSES else 0,
    )
    roast = c3.selectbox(
        t(lang, "add_roast"),
        [""] + ROAST_LEVELS,
        index=(ROAST_LEVELS.index(form["roast_level"]) + 1) if form.get("roast_level") in ROAST_LEVELS else 0,
    )
    roaster_notes = st.text_area(
        t(lang, "add_roaster_notes"),
        value=form.get("roaster_notes", ""),
        height=90,
    )
    if form.get("raw_text"):
        with st.expander(t(lang, "raw_ocr")):
            st.code(form["raw_text"])

    similar = st.session_state.get("pending_similar")
    if similar is None and name and roaster:
        similar = find_similar_beans(name, roaster)
    render_duplicate_warning(lang, similar or [])

    col_a, col_b = st.columns(2)
    save_new = col_a.button(t(lang, "save_bean"), type="primary", use_container_width=True)
    force = col_b.button(t(lang, "insert_anyway"), use_container_width=True)

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
            roaster_notes=roaster_notes,
            skip_fuzzy=force,
        )
        if result["status"] == "fuzzy":
            st.session_state.pending_similar = result["similar"]
            st.rerun()
        if result["status"] == "exists":
            st.warning(t(lang, "exists"))
            go_review(result["bean"]["id"])
            st.rerun()
        if result["status"] == "created":
            st.session_state.ocr_form = {}
            st.session_state.pending_similar = None
            st.toast(t(lang, "created"), icon="☕")
            go_review(result["bean"]["id"])
            st.rerun()


def main() -> None:
    init_db()
    inject_css()
    lang = st.session_state.get("lang", "da")
    filters = sidebar(lang)
    lang = filters["lang"]

    st.markdown(
        f'<div class="bn-hero"><h1>☕ {t(lang, "app_name")}</h1><p>{t(lang, "tagline")}</p></div>',
        unsafe_allow_html=True,
    )

    tab_keys = ["explore", "review", "add"]
    tab_labels = {
        "explore": f"☕ {t(lang, 'tab_explore')}",
        "review": f"✍️ {t(lang, 'tab_review')}",
        "add": f"➕ {t(lang, 'tab_add')}",
    }
    if st.session_state.get("active_tab") not in tab_keys:
        st.session_state.active_tab = "explore"
    current = st.radio(
        "nav",
        options=tab_keys,
        format_func=lambda key: tab_labels[key],
        key="active_tab",
        horizontal=True,
        label_visibility="collapsed",
    )
    if current == "explore":
        render_explore(lang, filters)
    elif current == "review":
        render_review(lang)
    else:
        render_add(lang)


if __name__ == "__main__":
    main()
