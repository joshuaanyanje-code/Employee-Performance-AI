import streamlit as st


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
        :root {{
            --safe-bottom: max(10px, env(safe-area-inset-bottom));
            --tap-size: 46px;
            --surface-glass: linear-gradient(160deg, rgba(19, 29, 49, 0.88), rgba(12, 20, 34, 0.88));
            --surface-border: rgba(148, 163, 184, 0.2);
            --accent: #3aa0ff;
        }}

        html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {{
            overflow-x: hidden;
        }}

        .block-container {{
            max-width: {max_width};
            margin: 0 auto;
            padding-top: 0.9rem;
            padding-bottom: calc(1.1rem + var(--safe-bottom));
        }}

        [data-testid="stVerticalBlock"] {{
            border: 1px solid var(--surface-border);
            border-radius: 14px;
            background: var(--surface-glass);
            box-shadow: 0 10px 24px rgba(0, 0, 0, 0.2);
        }}

        [data-testid="stMetric"] {{
            border-radius: 10px;
            border: 1px solid var(--surface-border);
            background: rgba(12, 20, 34, 0.7);
        }}

        button,
        .stButton > button,
        .stDownloadButton > button,
        .stFormSubmitButton > button {{
            min-height: var(--tap-size);
            border-radius: 12px;
        }}

        .stTextInput > div > div > input,
        .stTextArea textarea,
        .stSelectbox > div,
        .stDateInput > div,
        .stNumberInput input {{
            min-height: 42px;
            border-radius: 10px;
            border: 1px solid var(--surface-border);
            background: rgba(9, 15, 28, 0.75);
        }}

        [data-testid="stExpander"] {{
            border: 1px solid var(--surface-border);
            border-radius: 12px;
            overflow: hidden;
            background: rgba(10, 18, 32, 0.76);
        }}

        [data-testid="stExpander"] summary {{
            font-weight: 700;
            letter-spacing: 0.2px;
            color: #dbe8ff;
            background: linear-gradient(90deg, rgba(58, 160, 255, 0.16), rgba(58, 160, 255, 0.04));
            border-bottom: 1px solid var(--surface-border);
        }}

        [data-testid="stExpander"] summary:hover {{
            color: #eef5ff;
            box-shadow: inset 3px 0 0 var(--accent);
        }}

        [data-testid="stDataFrame"],
        [data-testid="stTable"] {{
            width: 100% !important;
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

            /* Do not force sidebar width on phones; allow true collapse. */
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
