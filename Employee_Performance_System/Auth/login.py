import streamlit as st
import pandas as pd
from database.db import get_connection, verify_password

def login_page():

    conn = get_connection()

    st.title("Team Intelligence System")

    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Login"):

        user = pd.read_sql(
            "SELECT * FROM users WHERE lower(trim(username)) = lower(trim(?)) ORDER BY organization",
            conn,
            params=(username,)
        )

        if not user.empty:
            # If multiple matches, show organization selector (optional for legacy login)
            if len(user) > 1:
                st.info("Multiple accounts found. Please use the main login interface.")
                return

            user_row = user.iloc[0]
            if not verify_password(password, user_row["password"]):
                st.error("Invalid password")
                return

            role = user_row["role"]
            org = user_row["organization"]
            branch = user_row.get("branch", "")

            st.session_state.user = username
            st.session_state.role = role
            st.session_state.organization = org
            st.session_state.branch = branch

            if role == "employee":
                st.session_state.page = "employee"
            elif role == "admin":
                st.session_state.page = "admin"
            elif role == "superadmin":
                st.session_state.page = "superadmin"

            st.rerun()
        else:
            st.error("User not found")

        else:
            st.error("Invalid login")
