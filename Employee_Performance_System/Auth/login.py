import streamlit as st
import pandas as pd
from database.db import get_connection

def login_page():

    conn = get_connection()

    st.title("Team Intelligence System")

    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Login"):

        user = pd.read_sql(
            "SELECT * FROM users WHERE username=? AND password=?",
            conn,
            params=(username, password)
        )

        if not user.empty:

            role = user.iloc[0]["role"]

            st.session_state.user = username
            st.session_state.role = role

            

            if role == "employee":
                st.session_state.page = "employee"

            elif role == "admin":
                st.session_state.page = "admin"

            elif role == "superadmin":
                st.session_state.page = "superadmin"

            st.rerun()

        else:
            st.error("Invalid login")
