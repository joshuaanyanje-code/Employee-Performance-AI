import streamlit as st
from html import escape
from calendar import monthrange
from datetime import date, datetime


def is_mobile_device():
    try:
        ua = ""
        ctx = getattr(st, "context", None)
        if ctx is not None:
            headers = getattr(ctx, "headers", None)
            if headers:
                ua = str(headers.get("user-agent") or headers.get("User-Agent") or "")

        ua_lower = ua.lower()
        mobile_markers = ["android", "iphone", "ipad", "mobile", "windows phone", "opera mini"]
        return bool(ua_lower and any(marker in ua_lower for marker in mobile_markers))
    except Exception:
        return False


def navigation_expander_open_default():
    """Open navigation by default on desktop, keep it collapsed on phones/tablets."""
    return not is_mobile_device()


def _render_pill_html(items):
    safe_items = [str(item).strip() for item in (items or []) if str(item).strip()]
    return "".join(f"<span class='app-role-pill'>{escape(item)}</span>" for item in safe_items)


def render_dashboard_banner(eyebrow, title, subtitle="", pills=None):
    st.markdown(
        f"""
        <section class="app-role-banner">
            <div class="app-role-eyebrow">{escape(str(eyebrow or 'Dashboard'))}</div>
            <div class="app-role-title">{escape(str(title or ''))}</div>
            {f'<div class="app-role-subtitle">{escape(str(subtitle or ""))}</div>' if subtitle else ''}
            {f'<div class="app-role-pills">{_render_pill_html(pills)}</div>' if pills else ''}
        </section>
        """,
        unsafe_allow_html=True,
    )


def _coerce_date_value(value, fallback=None):
    default_value = fallback or date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return default_value
    return default_value


def render_date_selector(label, key, value=None, disabled=False, on_change=None, min_value=None, max_value=None):
    fallback_value = _coerce_date_value(value, date.today())
    current_value = _coerce_date_value(st.session_state.get(key, value), fallback_value)
    min_date = _coerce_date_value(min_value, current_value) if min_value is not None else None
    max_date = _coerce_date_value(max_value, current_value) if max_value is not None else None
    if min_date is not None and current_value < min_date:
        current_value = min_date
    if max_date is not None and current_value > max_date:
        current_value = max_date

    sync_key = f"{key}_sync"
    current_token = current_value.isoformat()
    if st.session_state.get(sync_key) != current_token:
        st.session_state[f"{key}_year"] = current_value.year
        st.session_state[f"{key}_month"] = current_value.month
        st.session_state[f"{key}_day"] = current_value.day
        st.session_state[sync_key] = current_token

    base_year = (min_date.year if min_date is not None else date.today().year - 2)
    max_year = (max_date.year if max_date is not None else date.today().year + 6)
    year_options = list(range(base_year, max_year + 1))
    current_year = st.session_state.get(f"{key}_year", current_value.year)
    if current_year not in year_options:
        current_year = min(max(current_year, year_options[0]), year_options[-1])
        st.session_state[f"{key}_year"] = current_year

    st.markdown(label)
    col_year, col_month, col_day = st.columns(3)
    with col_year:
        selected_year = st.selectbox(
            "Year",
            year_options,
            index=year_options.index(current_year),
            key=f"{key}_year",
            disabled=disabled,
            label_visibility="collapsed",
            on_change=on_change,
        )

    month_start = min_date.month if min_date is not None and selected_year == min_date.year else 1
    month_end = max_date.month if max_date is not None and selected_year == max_date.year else 12
    month_options = list(range(month_start, month_end + 1))
    current_month = st.session_state.get(f"{key}_month", current_value.month)
    if current_month not in month_options:
        current_month = month_options[0]
        st.session_state[f"{key}_month"] = current_month
    with col_month:
        selected_month = st.selectbox(
            "Month",
            month_options,
            index=month_options.index(current_month),
            key=f"{key}_month",
            disabled=disabled,
            label_visibility="collapsed",
            format_func=lambda month_num: datetime(2000, month_num, 1).strftime("%b"),
            on_change=on_change,
        )

    max_day = monthrange(selected_year, selected_month)[1]
    day_start = min_date.day if min_date is not None and selected_year == min_date.year and selected_month == min_date.month else 1
    day_end = max_date.day if max_date is not None and selected_year == max_date.year and selected_month == max_date.month else max_day
    day_options = list(range(day_start, day_end + 1))
    current_day = st.session_state.get(f"{key}_day", current_value.day)
    if current_day not in day_options:
        current_day = day_options[0]
        st.session_state[f"{key}_day"] = current_day
    with col_day:
        selected_day = st.selectbox(
            "Day",
            day_options,
            index=day_options.index(current_day),
            key=f"{key}_day",
            disabled=disabled,
            label_visibility="collapsed",
            on_change=on_change,
        )

    selected_value = date(selected_year, selected_month, selected_day)
    st.session_state[key] = selected_value
    st.session_state[sync_key] = selected_value.isoformat()
    return selected_value


def render_date_range_selector(label, key, value=None, on_change=None):
    if isinstance(value, tuple) and len(value) == 2:
        default_start = _coerce_date_value(value[0])
        default_end = _coerce_date_value(value[1])
    else:
        single_value = _coerce_date_value(value, date.today())
        default_start = single_value
        default_end = single_value

    current_value = st.session_state.get(key, value)
    if isinstance(current_value, tuple) and len(current_value) == 2:
        start_value = _coerce_date_value(current_value[0], default_start)
        end_value = _coerce_date_value(current_value[1], default_end)
    else:
        start_value = default_start
        end_value = default_end

    st.markdown(label)
    start_col, end_col = st.columns(2)
    with start_col:
        start_date = render_date_selector("Start Date", f"{key}_start", value=start_value, on_change=on_change)
    with end_col:
        end_date = render_date_selector("End Date", f"{key}_end", value=end_value, on_change=on_change)

    st.session_state[key] = (start_date, end_date)
    return start_date, end_date


def apply_responsive_ui(mode="default"):
    """
    Apply mobile-first responsive styling across dashboards.
    mode:
      - default: standard dashboard pages
      - kiosk: compact kiosk layout
      - auth: login/auth pages
    """

    if mode == "kiosk":
        max_width = "560px"
    elif mode == "auth":
        max_width = "520px"
    else:
        max_width = "1240px"

    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700;900&family=Roboto+Flex:opsz,wght@8..144,400;8..144,500;8..144,700&display=swap');

        :root {{
            --safe-bottom: max(10px, env(safe-area-inset-bottom));
            --tap-size: 46px;
            --surface-glass: rgba(255, 255, 255, 0.9);
            --surface-border: rgba(15, 23, 42, 0.08);
            --accent: #0071e3;
            --text-main: #1d1d1f;
            --text-muted: #6e6e73;
        }}

        .app-role-banner {{
            margin: 0.2rem 0 1rem 0;
            padding: 1.05rem 1.2rem;
            border-radius: 24px;
            border: 1px solid rgba(15, 23, 42, 0.07);
            background: linear-gradient(135deg, rgba(255,255,255,0.98), rgba(244,248,255,0.96));
            box-shadow: 0 14px 34px rgba(15, 23, 42, 0.05);
        }}

        .app-role-eyebrow {{
            font-size: 0.72rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: var(--accent);
            margin-bottom: 0.25rem;
        }}

        .app-role-title {{
            font-size: clamp(1.2rem, 2vw, 1.85rem);
            line-height: 1.15;
            font-weight: 700;
            color: var(--text-main);
        }}

        .app-role-subtitle {{
            margin-top: 0.35rem;
            color: var(--text-muted);
            line-height: 1.55;
            font-size: 0.97rem;
        }}

        .app-role-pills {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            margin-top: 0.8rem;
        }}

        .app-role-pill {{
            display: inline-flex;
            align-items: center;
            padding: 0.36rem 0.72rem;
            border-radius: 999px;
            background: rgba(0, 113, 227, 0.08);
            border: 1px solid rgba(0, 113, 227, 0.10);
            color: var(--text-main);
            font-size: 0.86rem;
            font-weight: 500;
        }}

        html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {{
            overflow-x: hidden;
            font-family: 'Roboto', 'Roboto Flex', 'Segoe UI', sans-serif;
            color: var(--text-main);
        }}

        .block-container {{
            max-width: {max_width};
            margin: 0 auto;
            padding-top: 0.9rem;
            padding-bottom: calc(1.1rem + var(--safe-bottom));
        }}

        [data-testid="stMain"] [data-testid="stVerticalBlock"] {{
            border: 1px solid var(--surface-border);
            border-radius: 18px;
            background: var(--surface-glass);
            backdrop-filter: blur(14px);
            -webkit-backdrop-filter: blur(14px);
            box-shadow: 0 12px 30px rgba(15, 23, 42, 0.06);
        }}

        [data-testid="stMetric"] {{
            border-radius: 16px;
            border: 1px solid var(--surface-border);
            background: rgba(255, 255, 255, 0.96);
            box-shadow: 0 6px 16px rgba(15, 23, 42, 0.04);
        }}

        button,
        .stButton > button,
        .stDownloadButton > button,
        .stFormSubmitButton > button {{
            min-height: var(--tap-size);
            border-radius: 999px;
            font-family: 'Roboto', 'Roboto Flex', sans-serif;
            font-weight: 500;
        }}

        .stTextInput > div > div > input,
        .stTextArea textarea,
        .stDateInput > div,
        .stNumberInput input {{
            min-height: 42px;
            border-radius: 14px;
            border: 1px solid var(--surface-border);
            background: rgba(255, 255, 255, 0.96);
            color: var(--text-main);
        }}

        .stTimeInput input {{
            min-height: 42px;
            border-radius: 14px;
            border: 1px solid var(--surface-border) !important;
            background: rgba(255, 255, 255, 0.96) !important;
            color: var(--text-main) !important;
            caret-color: var(--text-main) !important;
            transition: box-shadow 0.28s cubic-bezier(0.22, 0.61, 0.36, 1), border-color 0.28s ease;
        }}

        .stTimeInput input:focus {{
            border-color: var(--accent) !important;
            box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.24), 0 0 0 10px rgba(0, 113, 227, 0.08) !important;
            animation: appTimeFocusPulse 1.65s cubic-bezier(0.22, 0.61, 0.36, 1) infinite;
        }}

        @keyframes appTimeFocusPulse {{
            0% {{
                box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.24), 0 0 0 10px rgba(0, 113, 227, 0.10);
            }}
            50% {{
                box-shadow: 0 0 0 6px rgba(0, 113, 227, 0.14), 0 0 0 16px rgba(0, 113, 227, 0.04);
            }}
            100% {{
                box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.24), 0 0 0 10px rgba(0, 113, 227, 0.10);
            }}
        }}

        .stSelectbox div[data-baseweb="select"] > div,
        .stMultiSelect div[data-baseweb="select"] > div {{
            min-height: 42px;
            border-radius: 14px;
            border: 1px solid var(--surface-border) !important;
            background: rgba(255, 255, 255, 0.98) !important;
            color: var(--text-main) !important;
        }}

        .stSelectbox div[data-baseweb="select"] input,
        .stMultiSelect div[data-baseweb="select"] input,
        .stSelectbox div[data-baseweb="select"] span,
        .stMultiSelect div[data-baseweb="select"] span {{
            color: var(--text-main) !important;
            -webkit-text-fill-color: var(--text-main) !important;
        }}

        .stSelectbox div[data-baseweb="select"] input,
        .stMultiSelect div[data-baseweb="select"] input {{
            caret-color: transparent !important;
        }}

        .stSelectbox svg,
        .stMultiSelect svg {{
            fill: var(--text-main) !important;
            color: var(--text-main) !important;
        }}

        div[data-baseweb="popover"],
        div[data-baseweb="popover"] ul,
        div[role="listbox"] {{
            background: #ffffff !important;
            color: var(--text-main) !important;
            border: 1px solid var(--surface-border) !important;
            box-shadow: 0 12px 28px rgba(15, 23, 42, 0.10) !important;
        }}

        div[role="option"] {{
            background: #ffffff !important;
            color: var(--text-main) !important;
        }}

        div[role="option"]:hover {{
            background: rgba(0, 113, 227, 0.08) !important;
        }}

        div[role="option"][aria-selected="true"] {{
            background: rgba(0, 113, 227, 0.14) !important;
            color: #005bb5 !important;
        }}

        [data-testid="stExpander"] {{
            border: 1px solid var(--surface-border);
            border-radius: 16px;
            overflow: hidden;
            background: rgba(255, 255, 255, 0.97);
        }}

        [data-testid="stExpander"] summary {{
            font-weight: 600;
            letter-spacing: 0;
            color: var(--text-main);
            background: linear-gradient(90deg, rgba(0, 113, 227, 0.08), rgba(255, 255, 255, 0.96));
            border-bottom: 1px solid var(--surface-border);
        }}

        [data-testid="stExpander"] summary:hover {{
            color: var(--text-main);
            box-shadow: inset 3px 0 0 var(--accent);
        }}

        [data-testid="stDataFrame"],
        [data-testid="stTable"] {{
            width: 100% !important;
        }}

        div[data-baseweb="notification"] {{
            border-radius: 16px !important;
            border: 1px solid var(--surface-border) !important;
            box-shadow: 0 8px 18px rgba(15, 23, 42, 0.04) !important;
            background: rgba(255, 255, 255, 0.96) !important;
        }}

        @media (min-width: 761px) {{
            [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{
                border: none;
                border-radius: 0;
                background: transparent;
                box-shadow: none;
            }}

            [data-testid="stSidebar"] .stRadio > div,
            [data-testid="stSidebar"] .stRadio label,
            [data-testid="stSidebar"] .stMarkdown,
            [data-testid="stSidebar"] .stSelectbox,
            [data-testid="stSidebar"] .stDateInput {{
                text-align: left;
                justify-content: flex-start;
                align-items: flex-start;
            }}
        }}

        @media (max-width: 1024px) {{
            .block-container {{
                max-width: 100%;
                padding-left: 0.9rem;
                padding-right: 0.9rem;
            }}
        }}

        @media (max-width: 760px) {{
            .block-container {{
                padding-top: 0.7rem;
                padding-left: 0.72rem;
                padding-right: 0.72rem;
            }}

            h1 {{ font-size: 1.45rem !important; }}
            h2 {{ font-size: 1.25rem !important; }}
            h3 {{ font-size: 1.08rem !important; }}

            [data-testid="column"] {{
                min-width: 0 !important;
            }}

            .stButton > button,
            .stDownloadButton > button,
            .stFormSubmitButton > button {{
                width: 100%;
                min-height: 44px;
                font-size: 0.98rem;
            }}

            [data-testid="stMetricValue"] {{
                font-size: 1.15rem !important;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
