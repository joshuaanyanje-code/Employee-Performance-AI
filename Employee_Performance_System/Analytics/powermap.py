import pandas as pd
import streamlit as st


def _pair_key(a, b):
    left = str(a or "").strip()
    right = str(b or "").strip()
    return tuple(sorted((left, right)))


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

    if df is None or df.empty:
        return insights

    work_df = df.copy()
    work_df["rater"] = work_df["rater"].astype(str).str.strip()
    work_df["rated"] = work_df["rated"].astype(str).str.strip()
    work_df = work_df[(work_df["rater"] != "") & (work_df["rated"] != "") & (work_df["rater"] != work_df["rated"])]

    if work_df.empty:
        return insights

    # =========================
    # PAIR RELATIONSHIP SUMMARY
    # =========================
    pair_df = work_df[["rater", "rated", "score"]].copy()
    pair_df["pair_key"] = pair_df.apply(lambda row: _pair_key(row["rater"], row["rated"]), axis=1)

    for pair_key, grp in pair_df.groupby("pair_key"):
        person_a, person_b = pair_key

        ab = grp[(grp["rater"] == person_a) & (grp["rated"] == person_b)]
        ba = grp[(grp["rater"] == person_b) & (grp["rated"] == person_a)]

        low_ab = int((ab["score"] < 45).sum())
        low_ba = int((ba["score"] < 45).sum())
        high_ab = int((ab["score"] > 85).sum())
        high_ba = int((ba["score"] > 85).sum())

        if low_ab >= 2 and low_ba >= 2:
            insights.append(
                f"⚠ Mutual conflict: {person_a} and {person_b} ({low_ab + low_ba} repeated low ratings)."
            )
        elif low_ab >= 2:
            insights.append(f"⚠ Tension: {person_a} repeatedly rates {person_b} very low ({low_ab} times).")
        elif low_ba >= 2:
            insights.append(f"⚠ Tension: {person_b} repeatedly rates {person_a} very low ({low_ba} times).")

        if high_ab >= 3 and high_ba >= 3 and low_ab == 0 and low_ba == 0:
            insights.append(
                f"🤝 Strong mutual alliance: {person_a} and {person_b} consistently rate each other highly."
            )
        elif ((high_ab >= 3 and low_ab == 0) or (high_ba >= 3 and low_ba == 0)) and not (low_ab >= 2 or low_ba >= 2):
            source = person_a if high_ab >= 3 else person_b
            target = person_b if source == person_a else person_a
            count = high_ab if source == person_a else high_ba
            insights.append(f"👍 Positive support: {source} consistently rates {target} highly ({count} times).")

    # =========================
    # FAVORITISM / TARGETING BY RATER
    # =========================
    for rater, sub in work_df.groupby("rater"):
        target_summary = (
            sub.groupby("rated")
            .agg(
                low_count=("score", lambda s: int((s < 40).sum())),
                high_count=("score", lambda s: int((s > 85).sum())),
            )
            .reset_index()
        )

        for _, row in target_summary.iterrows():
            target = str(row["rated"])
            low_count = int(row["low_count"])
            high_count = int(row["high_count"])

            if low_count >= 2 and high_count == 0:
                insights.append(f"🎯 Bias risk: {rater} targeting {target} ({low_count} low ratings).")
            elif high_count >= 3 and low_count == 0:
                insights.append(f"🤝 Favoritism risk: {rater} repeatedly favors {target} ({high_count} high ratings).")

    # =========================
    # TOXIC / EXTREME RATERS
    # =========================
    for rater, sub in work_df.groupby("rater"):
        avg_given = float(sub["score"].mean())

        if avg_given < 45:
            insights.append(f"🚨 {rater} is overly negative overall - possible toxic influence.")
        elif avg_given > 80 and len(sub) >= 3:
            insights.append(f"⚠ {rater} is overly positive overall - ratings may not be fully genuine.")

    # =========================
    # PERFORMANCE WATCHLIST
    # =========================
    avg_scores = work_df.groupby("rated")["score"].mean()

    for user, score in avg_scores.items():
        if score < 40:
            insights.append(f"❌ Performance risk: {user} is critically low ({score:.1f}%).")
        elif score < 50:
            insights.append(f"🚫 Performance risk: {user} needs urgent corrective action ({score:.1f}%).")
        elif score < 60:
            insights.append(f"⚠ Coaching need: {user} is below expected performance ({score:.1f}%).")

    # =========================
    # ADMIN IMPACT
    # =========================
    if users_df is not None and not users_df.empty and "role" in users_df.columns:
        admins = users_df[users_df["role"].astype(str).str.lower() == "admin"]["username"].astype(str)

        for admin in admins:
            score = avg_scores.get(admin, None)
            if score is None:
                continue
            if score >= 75:
                insights.append(f"👑 Admin impact: {admin} is improving branch performance.")
            elif score < 55:
                insights.append(f"🚨 Admin impact: {admin} may be weakening branch performance.")

    # =========================
    # ATTENDANCE + BEHAVIOR
    # =========================
    if attendance_df is not None and not attendance_df.empty and "clock_in" in attendance_df.columns:
        attendance_copy = attendance_df.copy()
        attendance_copy["clock_in"] = pd.to_datetime(attendance_copy["clock_in"], errors="coerce")
        late = attendance_copy[attendance_copy["clock_in"].dt.hour > 9]
        late_counts = late.groupby("username").size()

        for user, count in late_counts.items():
            if count >= 5:
                insights.append(f"⚠ Discipline issue: {user} has frequent lateness ({int(count)} times).")

    # =========================
    # ALLIANCE GROUPS (CLIQUE)
    # =========================
    strong_links = work_df[work_df["score"] > 85]
    if not strong_links.empty:
        group = strong_links.groupby("rater")["rated"].nunique()
        for user, count in group.items():
            if count >= 3:
                insights.append(f"👥 Clique signal: {user} forms unusually strong positive links with several people.")

    return list(dict.fromkeys(insights))


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
            groups[pair_key] = {
                "type": "pair",
                "members": [p1, p2],
                "pair_label": f"{p1} & {p2}",
                "logic_tags": ["pair", "synchronized"],
                "clock_in_count": counts["clock_in"],
                "clock_out_count": counts["clock_out"],
                "description": f"Synchronized attendance pair ({counts['clock_in']} clock-ins, {counts['clock_out']} clock-outs)",
            }
    
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
                        if pair_key in groups:
                            logic_tags = groups[pair_key].setdefault("logic_tags", [])
                            if "leave_sync" not in logic_tags:
                                logic_tags.append("leave_sync")
                            leave_count = int(groups[pair_key].get("leave_sync_count", 0) or 0) + 1
                            groups[pair_key]["leave_sync_count"] = leave_count
                            groups[pair_key]["description"] = (
                                groups[pair_key].get("description", "Pair detected") + f"; leave sync x{leave_count}"
                            )
    
    # =========================
    # DATING DETECTION (CROSS-GENDER PAIRS)
    # =========================
    if users_df is not None and not users_df.empty and not ratings_df.empty:
        
        gender_map = {}
        if "gender" in users_df.columns:
            gender_map = dict(zip(users_df["username"], users_df["gender"]))
        
        # High mutual ratings between opposite genders
        for pair_key, group_info in groups.items():
            if "synchronized" in group_info.get("logic_tags", []):
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
                            groups[pair_key]["relationship_type"] = "dating"
                            groups[pair_key]["gender_pair"] = [g1, g2]
                            groups[pair_key]["avg_mutual_rating"] = round((avg_r1_r2 + avg_r2_r1) / 2, 2)
                            groups[pair_key]["description"] = (
                                f"Cross-gender synchronized pair with strong mutual ratings ({avg_r1_r2:.0f}%/{avg_r2_r1:.0f}%)"
                            )
                            logic_tags = groups[pair_key].setdefault("logic_tags", [])
                            if "dating" not in logic_tags:
                                logic_tags.append("dating")
    
    # =========================
    # CONFLICTING PAIRS (LOW RATINGS)
    # =========================
    low_pair_ratings = ratings_df[ratings_df["score"] < 45]
    low_pairs = low_pair_ratings.groupby(["rater", "rated"]).size()
    
    for (rater, rated), count in low_pairs.items():
        if count >= 2:
            pair_key = f"{min(rater, rated)}-{max(rater, rated)}"
            if pair_key not in groups:
                groups[pair_key] = {
                    "type": "pair",
                    "members": [rater, rated],
                    "pair_label": f"{rater} & {rated}",
                    "logic_tags": ["pair"],
                }

            groups[pair_key]["conflict"] = True
            groups[pair_key]["low_ratings"] = count
            groups[pair_key]["conflict_direction"] = f"{rater} -> {rated}"
            groups[pair_key]["description"] = f"Conflict pair with {count} repeated low ratings under 45%"
            logic_tags = groups[pair_key].setdefault("logic_tags", [])
            if "conflict_pair" not in logic_tags:
                logic_tags.append("conflict_pair")

            insights.append(f"⚠ CONFLICT PAIR: {rater} & {rated} - {count} repeated low ratings (<45%)")
    
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
