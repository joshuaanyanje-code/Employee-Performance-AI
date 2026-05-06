import streamlit as st
from html import escape
from calendar import monthrange
from datetime import date, datetime


# ================================================================
# DEVICE DETECTION  (logic unchanged)
# ================================================================

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
    return not is_mobile_device()


# ================================================================
# DESIGN SYSTEM — inject_global_css()
# All visual tokens live here. Called once in app.py.
# ================================================================

def inject_global_css():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

        /* ---- Tokens ---- */
        :root {
            --onyx:      #0A0A0A;
            --cognac:    #B8895A;
            --champagne: #C9A876;
            --bone:      #F5F1EA;
            --smoke:     #6B6760;
            --paper:     #ffffff;
            --line:      #d6d8dd;
            --line-2:    #e6e7ea;
            --line-3:    #eef0f2;
            --fill:      #ececef;
            --fill-2:    #f5f6f7;
            --ink:       #1f2328;
            --ink-2:     #4b5563;
            --note-bg:   #fffbe8;
            --note-bd:   #f0e29e;
            --err-bg:    #fdecea;
            --err-bd:    #f3b9b3;
            --ok-bg:     #eaf6ee;
            --ok-bd:     #bcd9c4;
        }

        /* ---- Hide Streamlit chrome ---- */
        #MainMenu { visibility: hidden; }
        footer    { visibility: hidden; }
        header    { visibility: hidden; }
        [data-testid="stToolbar"]    { display: none !important; }
        [data-testid="stDecoration"] { display: none !important; }
        .stAppDeployButton           { display: none !important; }

        /* ---- Base typography ---- */
        html, body, [class*="css"] {
            font-family: 'Inter', ui-sans-serif, system-ui, sans-serif;
            color: var(--onyx);
            -webkit-font-smoothing: antialiased;
        }

        .mono {
            font-family: 'JetBrains Mono', ui-monospace, Menlo, monospace;
        }

        h1, h2, h3, h4 {
            font-family: 'Inter', sans-serif;
            font-weight: 700;
            letter-spacing: -0.01em;
            color: var(--onyx);
        }

        /* ---- Page background ---- */
        [data-testid="stAppViewContainer"],
        [data-testid="stMain"] {
            background: var(--bone) !important;
        }

        .block-container {
            max-width: 1240px;
            margin: 0 auto;
            padding-top: 0.6rem;
            padding-bottom: 2rem;
            background: transparent;
        }

        /* Remove glassmorphism from main blocks */
        [data-testid="stMain"] div[data-testid="stVerticalBlock"] {
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
            backdrop-filter: none !important;
            -webkit-backdrop-filter: none !important;
            padding: 0 !important;
            border-radius: 0 !important;
        }

        /* ---- Sidebar ---- */
        [data-testid="stSidebar"] {
            background: var(--paper) !important;
            border-right: 1px solid var(--line-2) !important;
        }

        [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
            padding: 0 !important;
        }

        /* ---- Inputs ---- */
        .stTextInput > div > div > input,
        .stTextArea textarea,
        .stNumberInput input {
            font-family: 'JetBrains Mono', monospace !important;
            font-size: 12px !important;
            height: 34px;
            border: 1px solid var(--line) !important;
            border-radius: 3px !important;
            background: var(--paper) !important;
            color: var(--ink-2) !important;
            padding: 0 10px !important;
        }

        .stTextInput > div > div > input:focus,
        .stTextArea textarea:focus {
            border-color: var(--cognac) !important;
            box-shadow: 0 0 0 2px rgba(184,137,90,0.15) !important;
            outline: none !important;
        }

        .stSelectbox div[data-baseweb="select"] > div,
        .stMultiSelect div[data-baseweb="select"] > div {
            min-height: 34px !important;
            border-radius: 3px !important;
            border: 1px solid var(--line) !important;
            background: var(--paper) !important;
            font-family: 'JetBrains Mono', monospace !important;
            font-size: 11.5px !important;
        }

        .stSelectbox div[data-baseweb="select"] input,
        .stMultiSelect div[data-baseweb="select"] input,
        .stSelectbox div[data-baseweb="select"] span,
        .stMultiSelect div[data-baseweb="select"] span {
            color: var(--ink-2) !important;
            -webkit-text-fill-color: var(--ink-2) !important;
            font-family: 'JetBrains Mono', monospace !important;
        }

        .stSelectbox svg, .stMultiSelect svg {
            fill: var(--smoke) !important;
        }

        div[data-baseweb="popover"],
        div[data-baseweb="popover"] ul,
        div[role="listbox"] {
            background: var(--paper) !important;
            border: 1px solid var(--line) !important;
            border-radius: 4px !important;
            box-shadow: 0 8px 20px rgba(10,10,10,0.10) !important;
        }

        div[role="option"] {
            font-family: 'JetBrains Mono', monospace !important;
            font-size: 11.5px !important;
            color: var(--ink-2) !important;
            background: var(--paper) !important;
        }

        div[role="option"]:hover {
            background: var(--fill-2) !important;
        }

        div[role="option"][aria-selected="true"] {
            background: var(--onyx) !important;
            color: #fff !important;
        }

        /* ---- Buttons (native Streamlit) ---- */
        .stButton > button,
        .stDownloadButton > button,
        .stFormSubmitButton > button {
            font-family: 'JetBrains Mono', monospace !important;
            font-size: 10.5px !important;
            font-weight: 500 !important;
            text-transform: uppercase !important;
            letter-spacing: 0.04em !important;
            padding: 5px 12px !important;
            border-radius: 3px !important;
            border: 1px solid var(--onyx) !important;
            background: var(--paper) !important;
            color: var(--onyx) !important;
            min-height: 32px !important;
            box-shadow: none !important;
            transition: background 0.15s, color 0.15s;
        }

        .stButton > button:hover {
            background: var(--fill-2) !important;
        }

        /* Primary button override — add class via st.markdown wrapper */
        .btn-primary-wrap .stButton > button {
            background: var(--onyx) !important;
            color: #fff !important;
            border-color: var(--onyx) !important;
        }

        .btn-primary-wrap .stButton > button:hover {
            background: #2a2a2a !important;
        }

        .btn-ghost-wrap .stButton > button {
            border-color: var(--line) !important;
            color: var(--smoke) !important;
        }

        .btn-ghost-wrap .stButton > button:hover {
            background: var(--fill-2) !important;
            color: var(--ink) !important;
        }

        /* Cognac accent button */
        .btn-cognac-wrap .stButton > button {
            background: var(--cognac) !important;
            color: #fff !important;
            border-color: var(--cognac) !important;
        }

        /* ---- Metrics → stat cards ---- */
        [data-testid="stMetric"] {
            background: var(--paper) !important;
            border: 1px solid var(--line) !important;
            border-radius: 4px !important;
            padding: 14px !important;
            box-shadow: none !important;
        }

        [data-testid="stMetricLabel"] {
            font-family: 'JetBrains Mono', monospace !important;
            font-size: 10.5px !important;
            font-weight: 600 !important;
            text-transform: uppercase !important;
            letter-spacing: 0.06em !important;
            color: var(--smoke) !important;
        }

        [data-testid="stMetricValue"] {
            font-family: 'Inter', sans-serif !important;
            font-size: 22px !important;
            font-weight: 600 !important;
            color: var(--onyx) !important;
            line-height: 1.2 !important;
        }

        [data-testid="stMetricDelta"] {
            font-family: 'JetBrains Mono', monospace !important;
            font-size: 10.5px !important;
        }

        /* ---- Expanders ---- */
        [data-testid="stExpander"] {
            border: 1px solid var(--line) !important;
            border-radius: 4px !important;
            background: var(--paper) !important;
            box-shadow: none !important;
        }

        [data-testid="stExpander"] summary {
            font-family: 'JetBrains Mono', monospace !important;
            font-size: 11.5px !important;
            font-weight: 600 !important;
            background: var(--fill-2) !important;
            color: var(--ink) !important;
            border-bottom: 1px solid var(--line-2) !important;
        }

        /* ---- Dataframes / tables ---- */
        [data-testid="stDataFrame"], [data-testid="stTable"] {
            width: 100% !important;
            border: 1px solid var(--line) !important;
            border-radius: 4px !important;
            overflow: hidden !important;
            font-family: 'JetBrains Mono', monospace !important;
        }

        /* ---- Camera input ---- */
        [data-testid="stCameraInput"] {
            border: 1px solid var(--line) !important;
            border-radius: 4px !important;
        }

        /* ---- Scrollbars ---- */
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: var(--fill-2); }
        ::-webkit-scrollbar-thumb { background: var(--line); border-radius: 3px; }

        /* ------------------------------------------------------------------ */
        /* LAYOUT PRIMITIVES                                                   */
        /* ------------------------------------------------------------------ */

        .app-topbar {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 0 10px;
            border-bottom: 1px solid var(--line-2);
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            color: var(--smoke);
            margin-bottom: 12px;
        }

        .app-topbar .right {
            margin-left: auto;
            display: flex;
            gap: 6px;
            align-items: center;
        }

        .crumbs {
            font-family: 'JetBrains Mono', monospace;
            font-size: 10.5px;
            color: var(--smoke);
        }

        .crumbs b {
            color: var(--ink-2);
            font-weight: 500;
        }

        .crumbs .arrow {
            color: var(--smoke);
            margin: 0 3px;
        }

        .h-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
        }

        .h-row h3 {
            margin: 0;
            font-size: 14px;
            font-weight: 600;
            color: var(--onyx);
        }

        .lbl-row {
            display: flex;
            justify-content: space-between;
            font-family: 'JetBrains Mono', monospace;
            font-size: 10.5px;
            color: var(--smoke);
            margin: 0 0 4px;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }

        .lbl-row a {
            color: var(--smoke);
            text-decoration: none;
        }

        .lbl-row a:hover {
            color: var(--ink);
        }

        .flex-row {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            align-items: center;
        }

        .flex-col {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        /* ------------------------------------------------------------------ */
        /* SIDEBAR NAV                                                         */
        /* ------------------------------------------------------------------ */

        .nav-brand {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 6px 8px 14px;
            border-bottom: 1px dashed var(--line);
            margin-bottom: 4px;
        }

        .nav-brand .sq {
            width: 22px;
            height: 22px;
            background: var(--onyx);
            border-radius: 3px;
            flex-shrink: 0;
        }

        .nav-brand b {
            font-size: 12.5px;
            font-family: 'Inter', sans-serif;
            font-weight: 600;
            color: var(--onyx);
        }

        .nav-section {
            margin-top: 12px;
        }

        .nav-section .nav-section-h {
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: var(--smoke);
            padding: 6px 8px;
            font-weight: 600;
            font-family: 'JetBrains Mono', monospace;
        }

        .nav-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 7px 10px;
            border-radius: 4px;
            font-size: 12px;
            color: var(--ink-2);
            cursor: pointer;
            font-family: 'Inter', sans-serif;
            margin-bottom: 1px;
            text-decoration: none;
        }

        .nav-item:hover {
            background: var(--fill-2);
            color: var(--onyx);
        }

        .nav-item.on {
            background: var(--onyx);
            color: #fff;
        }

        .nav-item .nav-l {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .nav-item .nav-ic {
            width: 12px;
            height: 12px;
            border: 1.5px solid var(--ink-2);
            border-radius: 2px;
            flex-shrink: 0;
        }

        .nav-item.on .nav-ic {
            border-color: #fff;
        }

        .nav-badge {
            background: var(--onyx);
            color: #fff;
            font-family: 'JetBrains Mono', monospace;
            font-size: 9.5px;
            padding: 1px 5px;
            border-radius: 99px;
        }

        .nav-item.on .nav-badge {
            background: #fff;
            color: var(--onyx);
        }

        /* ------------------------------------------------------------------ */
        /* APP-LEVEL TABS (Superadmin 3-tab strip above sidebar)               */
        /* ------------------------------------------------------------------ */

        .app-tabs-strip {
            display: flex;
            align-items: center;
            border-bottom: 1px solid var(--line-2);
            background: var(--fill-2);
            padding: 0 16px;
            margin: -0.6rem -1rem 0.8rem;
        }

        .app-tabs-strip .at {
            padding: 10px 14px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 10.5px;
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: var(--smoke);
            border-bottom: 2px solid transparent;
            cursor: pointer;
            white-space: nowrap;
        }

        .app-tabs-strip .at.on {
            color: var(--onyx);
            border-bottom-color: var(--cognac);
        }

        .app-tabs-strip .strip-right {
            margin-left: auto;
            display: flex;
            gap: 6px;
            align-items: center;
            padding: 6px 0;
        }

        /* ------------------------------------------------------------------ */
        /* SUB-TABS (within main content area)                                 */
        /* ------------------------------------------------------------------ */

        .subtabs {
            display: flex;
            gap: 2px;
            border-bottom: 1px solid var(--line-2);
            margin-bottom: 12px;
        }

        .subtabs .st-tab {
            padding: 7px 12px;
            font-size: 11.5px;
            color: var(--smoke);
            border-bottom: 2px solid transparent;
            font-family: 'JetBrains Mono', monospace;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            cursor: pointer;
        }

        .subtabs .st-tab.on {
            color: var(--onyx);
            border-bottom-color: var(--cognac);
        }

        /* ------------------------------------------------------------------ */
        /* CHIPS                                                                */
        /* ------------------------------------------------------------------ */

        .chip {
            font-family: 'JetBrains Mono', monospace;
            font-size: 10.5px;
            padding: 3px 8px;
            border: 1px solid var(--line);
            border-radius: 3px;
            color: var(--ink-2);
            background: var(--paper);
            white-space: nowrap;
            display: inline-block;
        }

        .chip.dark {
            background: var(--onyx);
            color: #fff;
            border-color: var(--onyx);
        }

        .chip.cognac {
            background: var(--cognac);
            color: #fff;
            border-color: var(--cognac);
        }

        /* ------------------------------------------------------------------ */
        /* BUTTONS (HTML)                                                       */
        /* ------------------------------------------------------------------ */

        .btn {
            font-family: 'JetBrains Mono', monospace;
            font-size: 10.5px;
            padding: 5px 10px;
            border: 1px solid var(--onyx);
            border-radius: 3px;
            background: var(--paper);
            color: var(--onyx);
            text-transform: uppercase;
            letter-spacing: 0.04em;
            cursor: pointer;
            display: inline-block;
            white-space: nowrap;
        }

        .btn.primary {
            background: var(--onyx);
            color: #fff;
            border-color: var(--onyx);
        }

        .btn.ghost {
            border-color: var(--line);
            color: var(--ink-2);
        }

        .btn.cognac {
            background: var(--cognac);
            color: #fff;
            border-color: var(--cognac);
        }

        /* ------------------------------------------------------------------ */
        /* TABLES                                                               */
        /* ------------------------------------------------------------------ */

        .app-table {
            border: 1px solid var(--line-2);
            border-radius: 4px;
            overflow: hidden;
            background: var(--paper);
            width: 100%;
            border-collapse: collapse;
            font-family: 'JetBrains Mono', monospace;
        }

        .app-table thead tr {
            background: var(--fill-2);
        }

        .app-table thead th {
            padding: 8px 12px;
            font-size: 10.5px;
            color: var(--smoke);
            text-transform: uppercase;
            letter-spacing: 0.06em;
            border-right: 1px dashed var(--line-2);
            border-bottom: 1px solid var(--line-2);
            font-weight: 600;
            text-align: left;
            white-space: nowrap;
        }

        .app-table thead th:last-child { border-right: none; }

        .app-table tbody tr {
            border-top: 1px solid var(--line-2);
        }

        .app-table tbody tr:hover {
            background: var(--fill-2);
        }

        .app-table tbody td {
            padding: 9px 12px;
            font-size: 11.5px;
            color: var(--ink-2);
            border-right: 1px dashed var(--line-3);
        }

        .app-table tbody td:last-child { border-right: none; }

        .skel {
            height: 9px;
            background: var(--fill);
            border-radius: 2px;
            width: 80%;
            display: inline-block;
        }
        .skel.s { width: 50%; }
        .skel.l { width: 95%; }

        .tbl-actions a {
            font-family: 'JetBrains Mono', monospace;
            font-size: 10.5px;
            color: var(--smoke);
            text-decoration: none;
            margin-right: 8px;
            cursor: pointer;
        }

        .tbl-actions a:hover { color: var(--onyx); }

        /* ------------------------------------------------------------------ */
        /* STAT CARDS                                                           */
        /* ------------------------------------------------------------------ */

        .stat-card {
            border: 1px solid var(--line);
            border-radius: 4px;
            padding: 14px;
            background: var(--paper);
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            gap: 4px;
        }

        .stat-card .stat-label {
            font-family: 'JetBrains Mono', monospace;
            font-size: 10.5px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: var(--smoke);
        }

        .stat-card .stat-value {
            font-family: 'Inter', sans-serif;
            font-size: 22px;
            font-weight: 600;
            color: var(--onyx);
            line-height: 1.2;
        }

        .stat-card .stat-sub {
            font-family: 'JetBrains Mono', monospace;
            font-size: 10.5px;
            color: var(--smoke);
        }

        /* ------------------------------------------------------------------ */
        /* NOTES / ALERTS                                                       */
        /* ------------------------------------------------------------------ */

        .note {
            display: flex;
            gap: 10px;
            background: var(--note-bg);
            border: 1px solid var(--note-bd);
            border-radius: 4px;
            padding: 10px 12px;
            font-size: 11.5px;
            color: #5b4a00;
            font-family: 'JetBrains Mono', monospace;
            line-height: 1.55;
            margin: 6px 0;
        }

        .note .note-pin {
            width: 18px;
            height: 18px;
            min-width: 18px;
            border-radius: 50%;
            background: #fff3a8;
            border: 1px solid #d6c351;
            display: grid;
            place-items: center;
            font-size: 10px;
            color: #5b4a00;
            font-weight: 700;
        }

        .note.err {
            background: var(--err-bg);
            border-color: var(--err-bd);
            color: #7a2f25;
        }

        .note.err .note-pin {
            background: #fadad6;
            border-color: #d8807a;
            color: #7a2f25;
        }

        .note.ok {
            background: var(--ok-bg);
            border-color: var(--ok-bd);
            color: #23532f;
        }

        .note.ok .note-pin {
            background: #cdebd5;
            border-color: #7faf8b;
            color: #23532f;
        }

        /* ------------------------------------------------------------------ */
        /* LOGIN LAYOUT                                                         */
        /* ------------------------------------------------------------------ */

        .login-frame {
            display: grid;
            grid-template-columns: 1fr 380px;
            min-height: 520px;
            border: 1px solid var(--line);
            border-radius: 6px;
            overflow: hidden;
            background: var(--paper);
            max-width: 860px;
            margin: 0 auto;
        }

        .login-frame .login-l {
            padding: 32px;
            border-right: 1px dashed var(--line);
            display: flex;
            flex-direction: column;
            gap: 14px;
            background: var(--fill-2);
        }

        .login-frame .login-r {
            padding: 34px 28px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            background: var(--paper);
        }

        .login-r h3 {
            margin: 0;
            font-size: 18px;
            font-weight: 600;
            color: var(--onyx);
        }

        .login-r .login-sub {
            font-size: 12.5px;
            color: var(--smoke);
            margin: 0;
        }

        .login-r .login-forgot {
            text-align: center;
            font-size: 11.5px;
            color: var(--smoke);
            text-decoration: none;
            font-family: 'JetBrains Mono', monospace;
            cursor: pointer;
        }

        .login-r .login-forgot:hover { color: var(--onyx); }

        .field-label {
            font-family: 'JetBrains Mono', monospace;
            font-size: 10px;
            color: var(--smoke);
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 2px;
            display: block;
        }

        /* ------------------------------------------------------------------ */
        /* KIOSK                                                                */
        /* ------------------------------------------------------------------ */

        .kiosk-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 16px;
            background: var(--onyx);
            color: #fff;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
        }

        .kiosk-body {
            padding: 28px 36px;
            min-height: 480px;
            background: var(--paper);
        }

        .kiosk-hero {
            text-align: center;
            padding: 24px 0 30px;
        }

        .kiosk-hero h1 {
            font-size: 30px;
            margin: 0;
            letter-spacing: -0.01em;
            color: var(--onyx);
        }

        .kiosk-hero p {
            margin: 6px 0 4px;
            color: var(--ink-2);
            font-size: 13.5px;
        }

        .kiosk-hero .kiosk-branch {
            font-family: 'JetBrains Mono', monospace;
            color: var(--smoke);
            font-size: 11.5px;
            margin-top: 8px;
        }

        .kiosk-cta {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 18px;
            max-width: 720px;
            margin: 0 auto;
        }

        .kiosk-btn {
            border: 2px solid var(--onyx);
            border-radius: 8px;
            padding: 34px 18px;
            text-align: center;
            font-size: 18px;
            font-weight: 600;
            font-family: 'Inter', sans-serif;
            color: var(--onyx);
            background: var(--paper);
            min-height: 64px;
            cursor: pointer;
            display: block;
            width: 100%;
        }

        .kiosk-btn.primary {
            background: var(--onyx);
            color: #fff;
        }

        .kiosk-btn .kiosk-desc {
            display: block;
            font-weight: 400;
            font-size: 12px;
            color: var(--smoke);
            margin-top: 6px;
            font-family: 'JetBrains Mono', monospace;
        }

        .kiosk-btn.primary .kiosk-desc {
            color: #cfcfcf;
        }

        .locked-banner {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 14px;
            background: #fff5d6;
            border: 1px dashed #d6b13d;
            border-radius: 4px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            color: #6e5400;
            margin-bottom: 16px;
        }

        .pin-dots {
            display: flex;
            gap: 10px;
            justify-content: center;
            margin: 14px 0;
        }

        .pin-dots i {
            width: 14px;
            height: 14px;
            border-radius: 50%;
            border: 1.5px solid var(--onyx);
            background: var(--paper);
            display: inline-block;
        }

        .pin-dots i.f {
            background: var(--onyx);
        }

        .pin-key {
            border: 1px solid var(--line) !important;
            border-radius: 6px !important;
            height: 54px !important;
            font-size: 20px !important;
            font-weight: 600 !important;
            background: var(--paper) !important;
            color: var(--onyx) !important;
        }

        /* Streamlit button override for kiosk PIN keys */
        .pin-key-wrap .stButton > button {
            border: 1px solid var(--line) !important;
            border-radius: 6px !important;
            height: 54px !important;
            width: 100% !important;
            font-size: 20px !important;
            font-weight: 600 !important;
            font-family: 'Inter', sans-serif !important;
            background: var(--paper) !important;
            color: var(--onyx) !important;
            text-transform: none !important;
            letter-spacing: 0 !important;
            padding: 0 !important;
        }

        .kiosk-btn-wrap .stButton > button {
            border: 2px solid var(--onyx) !important;
            border-radius: 8px !important;
            min-height: 64px !important;
            width: 100% !important;
            font-size: 16px !important;
            font-weight: 600 !important;
            font-family: 'Inter', sans-serif !important;
            background: var(--paper) !important;
            color: var(--onyx) !important;
            text-transform: none !important;
            letter-spacing: 0 !important;
        }

        .kiosk-btn-primary-wrap .stButton > button {
            border: 2px solid var(--onyx) !important;
            border-radius: 8px !important;
            min-height: 64px !important;
            width: 100% !important;
            font-size: 16px !important;
            font-weight: 600 !important;
            font-family: 'Inter', sans-serif !important;
            background: var(--onyx) !important;
            color: #fff !important;
            text-transform: none !important;
            letter-spacing: 0 !important;
        }

        /* ------------------------------------------------------------------ */
        /* ILLUSTRATION / PLACEHOLDER BOXES                                    */
        /* ------------------------------------------------------------------ */

        .box-placeholder {
            border: 1.5px dashed var(--line);
            background: repeating-linear-gradient(
                135deg, #f7f8f9 0 8px, #ffffff 8px 16px
            );
            color: var(--smoke);
            font-family: 'JetBrains Mono', monospace;
            font-size: 11.5px;
            display: flex;
            align-items: center;
            justify-content: center;
            text-align: center;
            padding: 14px;
            border-radius: 4px;
            min-height: 60px;
        }

        .box-placeholder.xl { min-height: 200px; }

        /* ------------------------------------------------------------------ */
        /* DASHBOARD BANNER (updated from old style)                           */
        /* ------------------------------------------------------------------ */

        .app-banner {
            margin: 0 0 1rem 0;
            padding: 14px 16px;
            border: 1px solid var(--line);
            border-radius: 4px;
            background: var(--paper);
            display: flex;
            flex-direction: column;
            gap: 4px;
        }

        .app-banner-eyebrow {
            font-family: 'JetBrains Mono', monospace;
            font-size: 10.5px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: var(--cognac);
        }

        .app-banner-title {
            font-size: 18px;
            font-weight: 700;
            color: var(--onyx);
            letter-spacing: -0.01em;
        }

        .app-banner-subtitle {
            font-size: 12.5px;
            color: var(--smoke);
            margin-top: 2px;
        }

        .app-banner-pills {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-top: 6px;
        }

        .app-banner-pill {
            display: inline-flex;
            align-items: center;
            padding: 2px 7px;
            border-radius: 3px;
            background: var(--onyx);
            color: #fff;
            font-family: 'JetBrains Mono', monospace;
            font-size: 10.5px;
        }

        /* ------------------------------------------------------------------ */
        /* PAGINATION                                                           */
        /* ------------------------------------------------------------------ */

        .pagination-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-family: 'JetBrains Mono', monospace;
            font-size: 10.5px;
            color: var(--smoke);
            margin-top: 6px;
        }

        /* ------------------------------------------------------------------ */
        /* MOBILE RESPONSIVE                                                   */
        /* ------------------------------------------------------------------ */

        @media (max-width: 760px) {
            .block-container {
                padding-left: 0.6rem;
                padding-right: 0.6rem;
            }

            .login-frame {
                grid-template-columns: 1fr;
            }

            .login-frame .login-l {
                display: none;
            }

            .kiosk-cta {
                grid-template-columns: 1fr;
            }

            h1 { font-size: 1.4rem !important; }
            h2 { font-size: 1.2rem !important; }
            h3 { font-size: 1rem !important; }

            .app-table thead th,
            .app-table tbody td {
                font-size: 10px;
                padding: 6px 8px;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ================================================================
# RENDER HELPERS
# ================================================================

def _render_pill_html(items):
    safe_items = [str(item).strip() for item in (items or []) if str(item).strip()]
    return "".join(
        f"<span class='app-banner-pill'>{escape(item)}</span>"
        for item in safe_items
    )


def render_dashboard_banner(eyebrow, title, subtitle="", pills=None):
    """Updated banner using new design system."""
    st.markdown(
        f"""
        <div class="app-banner">
            <div class="app-banner-eyebrow">{escape(str(eyebrow or 'Dashboard'))}</div>
            <div class="app-banner-title">{escape(str(title or ''))}</div>
            {f'<div class="app-banner-subtitle">{escape(str(subtitle or ""))}</div>' if subtitle else ''}
            {f'<div class="app-banner-pills">{_render_pill_html(pills)}</div>' if pills else ''}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_topbar(breadcrumb_parts, chips=None):
    """
    Render the wireframe topbar with breadcrumbs and optional chips.
    breadcrumb_parts: list of strings — last item is bold.
    chips: list of (label, dark) tuples.
    """
    crumb_html = ""
    for i, part in enumerate(breadcrumb_parts):
        if i > 0:
            crumb_html += '<span class="arrow"> / </span>'
        if i == len(breadcrumb_parts) - 1:
            crumb_html += f"<b>{escape(str(part))}</b>"
        else:
            crumb_html += escape(str(part))

    chips_html = ""
    if chips:
        for label, dark in chips:
            cls = "chip dark" if dark else "chip"
            chips_html += f'<span class="{cls}">{escape(str(label))}</span>'

    st.markdown(
        f"""
        <div class="app-topbar">
            <span class="crumbs">{crumb_html}</span>
            <div class="right">{chips_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_note(text, kind="info", pin="i"):
    """Render a wireframe-style annotation note. kind: info | err | ok"""
    css_cls = {"info": "note", "err": "note err", "ok": "note ok"}.get(kind, "note")
    st.markdown(
        f"""
        <div class="{css_cls}">
            <span class="note-pin">{escape(str(pin))}</span>
            <div>{text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_stat_card(label, value, sub=None):
    """Render a single stat card as HTML."""
    sub_html = f'<div class="stat-sub">{escape(str(sub))}</div>' if sub else ""
    return f"""
        <div class="stat-card">
            <div class="stat-label">{escape(str(label))}</div>
            <div class="stat-value">{escape(str(value))}</div>
            {sub_html}
        </div>
    """


def render_nav_item(label, page_key, current_page, badge=None):
    """Render a single sidebar nav item HTML string."""
    is_active = current_page == page_key
    active_cls = " on" if is_active else ""
    badge_html = f'<span class="nav-badge">{escape(str(badge))}</span>' if badge is not None else ""
    return f"""
        <div class="nav-item{active_cls}" onclick="">
            <span class="nav-l">
                <span class="nav-ic"></span>
                {escape(str(label))}
            </span>
            {badge_html}
        </div>
    """


def render_sidebar_nav(brand_name, sections, current_page):
    """
    Render the full sidebar nav.
    sections: list of {"header": str, "items": [{"label": str, "key": str, "badge": int|None}]}
    Returns HTML string — call st.sidebar.markdown(html, unsafe_allow_html=True).
    """
    html = f"""
        <div class="nav-brand">
            <div class="sq"></div>
            <b>{escape(str(brand_name))}</b>
        </div>
    """
    for section in sections:
        html += f'<div class="nav-section">'
        if section.get("header"):
            html += f'<div class="nav-section-h">{escape(str(section["header"]))}</div>'
        for item in section.get("items", []):
            html += render_nav_item(
                item["label"],
                item["key"],
                current_page,
                badge=item.get("badge"),
            )
        html += "</div>"
    return html


def render_app_tabs_strip(tabs, current_tab, right_html=""):
    """
    Render the superadmin 3-tab strip above the sidebar.
    tabs: list of (label, key)
    Returns HTML string.
    """
    tabs_html = ""
    for label, key in tabs:
        active_cls = " on" if current_tab == key else ""
        tabs_html += f'<span class="at{active_cls}">{escape(str(label))}</span>'
    return f"""
        <div class="app-tabs-strip">
            {tabs_html}
            <div class="strip-right">{right_html}</div>
        </div>
    """


def render_subtabs(tabs, current_tab):
    """Render in-page sub-tabs. tabs: list of (label, key)."""
    html = '<div class="subtabs">'
    for label, key in tabs:
        active_cls = " on" if current_tab == key else ""
        html += f'<span class="st-tab{active_cls}">{escape(str(label))}</span>'
    html += "</div>"
    return html


def render_locked_banner(text):
    return f'<div class="locked-banner">&#x26D3; {escape(str(text))}</div>'


def render_pin_dots(filled, total=4):
    dots = ""
    for i in range(total):
        cls = "f" if i < filled else ""
        dots += f'<i class="{cls}"></i>'
    return f'<div class="pin-dots">{dots}</div>'


# ================================================================
# APPLY_RESPONSIVE_UI — kept for backward compat, delegates to new CSS
# ================================================================

def apply_responsive_ui(mode="default"):
    inject_global_css()


# ================================================================
# DATE SELECTORS (logic fully unchanged, just restyled by CSS)
# ================================================================

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
            "Year", year_options,
            index=year_options.index(current_year),
            key=f"{key}_year", disabled=disabled,
            label_visibility="collapsed", on_change=on_change,
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
            "Month", month_options,
            index=month_options.index(current_month),
            key=f"{key}_month", disabled=disabled,
            label_visibility="collapsed",
            format_func=lambda m: datetime(2000, m, 1).strftime("%b"),
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
            "Day", day_options,
            index=day_options.index(current_day),
            key=f"{key}_day", disabled=disabled,
            label_visibility="collapsed", on_change=on_change,
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
