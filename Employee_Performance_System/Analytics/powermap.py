import pandas as pd
import streamlit as st

# Safe imports
try:
    import plotly.graph_objects as go
except ImportError:
    go = None

try:
    import networkx as nx
except ImportError:
    nx = None


# =====================================================
# GRAPH (UNCHANGED CORE ✅)
# =====================================================
def build_relationship_graph(df):

    if nx is None or go is None:
        return None

    if df is None or df.empty:
        return None

    G = nx.Graph()

    for _, row in df.iterrows():
        G.add_edge(row["rater"], row["rated"], weight=row["score"])

    pos = nx.spring_layout(G)

    edge_x, edge_y = [], []

    for edge in G.edges():
        x0, y0 = pos[edge[0]]
        x1, y1 = pos[edge[1]]
        edge_x.extend([x0, x1, None])
        edge_y.extend([y0, y1, None])

    edge_trace = go.Scatter(
        x=edge_x, y=edge_y,
        line=dict(width=1, color='#888'),
        hoverinfo="none",
        mode="lines"
    )

    node_x, node_y, labels = [], [], []

    for node in G.nodes():
        x, y = pos[node]
        node_x.append(x)
        node_y.append(y)
        labels.append(node)

    node_trace = go.Scatter(
        x=node_x, y=node_y,
        mode="markers+text",
        text=labels,
        textposition="top center",
        marker=dict(size=20)
    )

    fig = go.Figure(
        data=[edge_trace, node_trace],
        layout=go.Layout(showlegend=False)
    )

    return fig


# =====================================================
# 🔥 RELATIONSHIP ANALYZER (NEW CORE)
# =====================================================
def analyze_relationships(df, users_df=None, attendance_df=None):

    insights = []

    if df.empty:
        return insights

    # =========================
    # CONFLICT DETECTION
    # =========================
    conflicts = df[df["score"] < 45]

    conflict_counts = conflicts.groupby(["rater", "rated"]).size()

    for (rater, rated), count in conflict_counts.items():
        if count >= 2:
            insights.append(f"⚠ Conflict: {rater} vs {rated} ({count} times)")

    # =========================
    # FAVORITISM (HIGH SCORES)
    # =========================
    fav = df[df["score"] > 85]
    fav_counts = fav.groupby(["rater", "rated"]).size()

    for (rater, rated), count in fav_counts.items():
        if count >= 3:
            insights.append(f"🤝 Favoritism: {rater} → {rated} ({count} times)")

    # =========================
    # BIAS / TARGETING
    # =========================
    for rater in df["rater"].unique():

        sub = df[df["rater"] == rater]

        low_targets = sub[sub["score"] < 40]["rated"].value_counts()

        for target, count in low_targets.items():
            if count >= 2:
                insights.append(f"🎯 Bias: {rater} targeting {target} ({count} low ratings)")

    # =========================
    # TOXIC RATERS
    # =========================
    for rater in df["rater"].unique():

        sub = df[df["rater"] == rater]

        if sub["score"].mean() < 45:
            insights.append(f"🚨 {rater} is overly negative → Toxic influence")

        if sub["score"].mean() > 80:
            insights.append(f"⚠ {rater} overly positive → Not genuine")

    # =========================
    # HR ACTION ENGINE 🔥
    # =========================
    avg_scores = df.groupby("rated")["score"].mean()

    for user, score in avg_scores.items():

        if score < 40:
            insights.append(f"❌ {user} → FIRE / REPLACE (very low performance)")

        elif score < 50:
            insights.append(f"🚫 {user} → SUSPEND or strict warning")

        elif score < 60:
            insights.append(f"⚠ {user} → Needs coaching / improvement")

    # =========================
    # ADMIN IMPACT
    # =========================
    if users_df is not None:

        admins = users_df[users_df["role"] == "admin"]["username"]

        for admin in admins:

            score = avg_scores.get(admin, 0)

            if score >= 75:
                insights.append(f"👑 Admin {admin} improving branch performance")

            elif score < 55:
                insights.append(f"🚨 Admin {admin} weakening branch performance")

    # =========================
    # ATTENDANCE + BEHAVIOR
    # =========================
    if attendance_df is not None and not attendance_df.empty:

        attendance_df["clock_in"] = pd.to_datetime(attendance_df["clock_in"], errors="coerce")

        late = attendance_df[attendance_df["clock_in"].dt.hour > 9]

        late_counts = late.groupby("username").size()

        for user, count in late_counts.items():

            if count >= 5:
                insights.append(f"⚠ {user} frequent lateness → Discipline issue")

    # =========================
    # ALLIANCE GROUPS (CLIQUE)
    # =========================
    strong_links = df[df["score"] > 85]

    if not strong_links.empty:

        group = strong_links.groupby("rater")["rated"].nunique()

        for user, count in group.items():
            if count >= 3:
                insights.append(f"👥 {user} forming strong alliances (possible clique)")

    return list(set(insights))


# =====================================================
# 🔥 DEEP GROUP DETECTION (CLIQUES, PAIRS, RELATIONSHIPS)
# =====================================================
def detect_synchronized_groups(attendance_df, ratings_df, users_df=None, leaves_df=None):
    """Detects groups based on synchronized clocking, ratings, and leave patterns."""
    
    insights = []
    groups = {}  # Store detected groups
    
    if attendance_df is None or attendance_df.empty:
        return insights, groups
    
    attendance_df = attendance_df.copy()
    attendance_df["clock_in"] = pd.to_datetime(attendance_df["clock_in"], errors="coerce")
    attendance_df["clock_out"] = pd.to_datetime(attendance_df["clock_out"], errors="coerce")
    attendance_df["date"] = pd.to_datetime(attendance_df["date"], errors="coerce")
    
    # =========================
    # SYNCHRONIZED CLOCKING (5 MIN OR LESS)
    # =========================
    clock_pairs = []
    
    dates = attendance_df["date"].unique()
    for date in dates:
        day_records = attendance_df[attendance_df["date"] == date].sort_values("clock_in")
        
        for i in range(len(day_records)):
            for j in range(i + 1, len(day_records)):
                r1 = day_records.iloc[i]
                r2 = day_records.iloc[j]
                
                if pd.notna(r1["clock_in"]) and pd.notna(r2["clock_in"]):
                    time_diff = (r2["clock_in"] - r1["clock_in"]).total_seconds() / 60
                    
                    if 0 < time_diff <= 5:
                        pair = tuple(sorted([r1["username"], r2["username"]]))
                        clock_pairs.append((pair, "clock_in", date))
        
        # SYNCHRONIZED CLOCK OUT
        day_records_out = attendance_df[attendance_df["date"] == date].sort_values("clock_out")
        for i in range(len(day_records_out)):
            for j in range(i + 1, len(day_records_out)):
                r1 = day_records_out.iloc[i]
                r2 = day_records_out.iloc[j]
                
                if pd.notna(r1["clock_out"]) and pd.notna(r2["clock_out"]):
                    time_diff = (r2["clock_out"] - r1["clock_out"]).total_seconds() / 60
                    
                    if 0 < time_diff <= 5:
                        pair = tuple(sorted([r1["username"], r2["username"]]))
                        clock_pairs.append((pair, "clock_out", date))
    
    # Count synchronized pairs
    pair_freq = {}
    for pair, action, date in clock_pairs:
        key = f"{pair[0]}-{pair[1]}"
        if key not in pair_freq:
            pair_freq[key] = {"clock_in": 0, "clock_out": 0}
        pair_freq[key][action] += 1
    
    # Flag groups that clock together & out together
    for pair_key, counts in pair_freq.items():
        if counts["clock_in"] >= 3 or counts["clock_out"] >= 3:
            p1, p2 = pair_key.split("-")
            insights.append(f"👥 GROUP DETECTED: {p1} & {p2} clock in/out together ({counts['clock_in']} in, {counts['clock_out']} out)")
            groups[pair_key] = {"type": "synchronized", "members": [p1, p2]}
    
    # =========================
    # SYNCHRONIZED LEAVE PATTERNS
    # =========================
    if leaves_df is not None and not leaves_df.empty:
        leaves_df_copy = leaves_df.copy()
        leaves_df_copy["leave_start"] = pd.to_datetime(leaves_df_copy.get("leave_start", leaves_df_copy.get("date", None)), errors="coerce")
        
        users_leaving = leaves_df_copy.groupby("leave_start")["username"].apply(list)
        
        for date, users in users_leaving.items():
            if len(users) >= 2:
                for i, u1 in enumerate(users):
                    for u2 in users[i + 1:]:
                        pair_key = f"{min(u1, u2)}-{max(u1, u2)}"
                        insights.append(f"🌴 {u1} & {u2} take leave on same day → Possible group")
    
    # =========================
    # DATING DETECTION (CROSS-GENDER PAIRS)
    # =========================
    if users_df is not None and not users_df.empty and not ratings_df.empty:
        
        gender_map = {}
        if "gender" in users_df.columns:
            gender_map = dict(zip(users_df["username"], users_df["gender"]))
        
        # High mutual ratings between opposite genders
        for pair_key, group_info in groups.items():
            if group_info["type"] == "synchronized":
                p1, p2 = group_info["members"]
                
                # Check if opposite gender
                g1 = gender_map.get(p1, "Unknown")
                g2 = gender_map.get(p2, "Unknown")
                
                if g1 != "Unknown" and g2 != "Unknown" and g1 != g2:
                    # Check ratings between them
                    r1_to_r2 = ratings_df[(ratings_df["rater"] == p1) & (ratings_df["rated"] == p2)]
                    r2_to_r1 = ratings_df[(ratings_df["rater"] == p2) & (ratings_df["rated"] == p1)]
                    
                    if not r1_to_r2.empty and not r2_to_r1.empty:
                        avg_r1_r2 = r1_to_r2["score"].mean()
                        avg_r2_r1 = r2_to_r1["score"].mean()
                        
                        if avg_r1_r2 > 78 and avg_r2_r1 > 78:
                            insights.append(f"💑 DATING/RELATIONSHIP DETECTED: {p1} ({g1}) & {p2} ({g2}) - Synchronized + High mutual ratings (>{avg_r1_r2:.0f}%)")
                            groups[pair_key]["relationship"] = True
    
    # =========================
    # CONFLICTING PAIRS (LOW RATINGS)
    # =========================
    low_pair_ratings = ratings_df[ratings_df["score"] < 45]
    low_pairs = low_pair_ratings.groupby(["rater", "rated"]).size()
    
    for (rater, rated), count in low_pairs.items():
        if count >= 2:
            pair_key = f"{min(rater, rated)}-{max(rater, rated)}"
            if pair_key not in groups:
                groups[pair_key] = {"type": "conflict_pair", "members": [rater, rated], "low_ratings": count}
                insights.append(f"⚠ CONFLICT PAIR: {rater} & {rated} - {count} mutual low ratings (<45%)")
    
    return list(set(insights)), groups


# =====================================================
# DISPLAY (UNCHANGED + UPGRADE)
# =====================================================
def display_powermap(df, users_df=None, attendance_df=None):

    if go is None or nx is None:
        st.warning("Graph disabled (missing Plotly/NetworkX)")
        return

    # GRAPH
    fig = build_relationship_graph(df)

    if fig:
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No relationship data")

    # 🔥 INSIGHTS PANEL
    insights = analyze_relationships(df, users_df, attendance_df)

    if insights:
        st.subheader("🧠 Relationship Intelligence")
        for i in insights:
            st.write("•", i)
