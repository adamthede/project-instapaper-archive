import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path

# Config
st.set_page_config(page_title="Instapaper Archive", layout="wide", initial_sidebar_state="expanded")
DATA_DIR = Path(__file__).parent.parent / "data"
INDEX_PATH = DATA_DIR / "archive_index.parquet"

# Custom CSS for "Premium Dark" look
st.markdown("""
<style>
    .stApp {
        background-color: #0E1117;
        color: #FAFAFA;
    }
    .stMetric {
        background-color: #262730;
        padding: 15px;
        border-radius: 5px;
    }
    h1, h2, h3 {
        font-family: 'Helvetica Neue', sans-serif;
        font-weight: 300;
    }
    .stExpander {
        border: 1px solid #444;
        border-radius: 5px;
    }
</style>
""", unsafe_allow_html=True)

@st.cache_data
def load_data():
    if not INDEX_PATH.exists():
        st.error("Index file not found. Please run `scripts/build_index.py`.")
        return pd.DataFrame()
    return pd.read_parquet(INDEX_PATH)

def main():
    st.title("📚 Instapaper Archive Analytics")

    df = load_data()
    if df.empty:
        return

    # Sidebar Navigation
    page = st.sidebar.radio(
        "Navigation",
        [
            "The Quantified Reader",
            "Content Intelligence",
            "Network & Entities",
            "Concept Explorer",
            "Archive Explorer",
        ],
    )

    # Global Sidebar Filters
    st.sidebar.markdown("---")
    st.sidebar.subheader("Filter Archive")

    # Date Range
    min_date = df["date_saved"].min().date()
    max_date = df["date_saved"].max().date()

    date_range = st.sidebar.date_input(
        "Date Range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )

    # Filter Data based on Date
    if len(date_range) == 2:
        mask = (df["date_saved"].dt.date >= date_range[0]) & (df["date_saved"].dt.date <= date_range[1])
        df_filtered = df.loc[mask]
    else:
        df_filtered = df

    if page == "The Quantified Reader":
        render_overview(df_filtered)
    elif page == "Content Intelligence":
        render_intelligence(df_filtered)
    elif page == "Network & Entities":
        render_network(df_filtered)
    elif page == "Concept Explorer":
        render_concept_explorer(df_filtered)
    elif page == "Archive Explorer":
        render_explorer(df_filtered)

def render_overview(df):
    st.header("The Quantified Reader")

    # Top Level Metrics
    c1, c2, c3, c4 = st.columns(4)

    total_articles = len(df)
    total_words = df["word_count"].sum()
    hours_read = round(df["reading_time_min"].sum() / 60, 1)
    avg_complexity = df["grade_level"].mean() if "grade_level" in df.columns else 0

    c1.metric("Articles Archived", f"{total_articles:,}")
    c2.metric("Words Read", f"{total_words/1000000:.2f}M")
    c3.metric("Reading Time (Hours)", f"{hours_read:,}")
    c4.metric("Avg. Grade Level", f"{avg_complexity:.1f}")

    # Timeline
    st.subheader("Reading Activity Over Time")
    # Resample by month-end
    timeline = df.set_index("date_saved").resample("ME").size().reset_index(name="count")

    fig = px.bar(
        timeline,
        x="date_saved",
        y="count",
        title="Articles Saved per Month",
        labels={"date_saved": "Date", "count": "Articles"},
        template="plotly_dark",
    )
    fig.update_traces(marker_color="#FF4B4B")
    st.plotly_chart(fig, use_container_width=True)

    # Reading Rhythms
    st.subheader("Reading Rhythms")
    c1, c2 = st.columns(2)

    with c1:
        # Day of Week Analysis
        df["day_of_week"] = df["date_saved"].dt.day_name()
        days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        day_counts = df["day_of_week"].value_counts().reindex(days_order).reset_index()
        day_counts.columns = ["Day", "Count"]

        fig_day = px.bar(day_counts, x="Day", y="Count", title="Activity by Day of Week", template="plotly_dark")
        st.plotly_chart(fig_day, use_container_width=True)

    with c2:
        # Complexity over Time
        if "grade_level" in df.columns:
            complexity = df.set_index("date_saved")["grade_level"].resample("ME").mean().reset_index()
            fig_comp = px.line(
                complexity,
                x="date_saved",
                y="grade_level",
                title="Reading Complexity (Flesch-Kincaid Grade)",
                template="plotly_dark",
            )
            st.plotly_chart(fig_comp, use_container_width=True)

    # Habits
    st.subheader("Sources & Habits")
    c1, c2 = st.columns(2)

    with c1:
        st.subheader("Top Authors")
        top_authors = df["author"].value_counts().head(10).reset_index()
        top_authors.columns = ["Author", "Count"]
        fig_auth = px.bar(top_authors, x="Count", y="Author", orientation="h", template="plotly_dark")
        fig_auth.update_layout(yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig_auth, use_container_width=True)

    with c2:
        st.subheader("Word Count Distribution")
        fig_hist = px.histogram(df, x="word_count", nbins=50, title="Article Lengths", template="plotly_dark")
        st.plotly_chart(fig_hist, use_container_width=True)

def render_intelligence(df):
    st.header("Content Intelligence")

    # Check if enrichment exists
    enriched_count = df["topics"].apply(lambda x: x is not None and len(x) > 0).sum()
    if enriched_count == 0:
        st.warning("No AI enrichment data found. Please run `scripts/enrich_archive.py` to generate insights.")
        return

    # Sentiment & Emotion
    st.subheader("Emotional Landscape")
    c1, c2 = st.columns(2)

    with c1:
        if "sentiment" in df.columns:
            # Normalize sentiment so this chart focuses on
            # Positive / Negative / Neutral, even if earlier
            # enrichment runs produced richer emotion labels.
            def canonicalize_sentiment(val):
                if not isinstance(val, str):
                    return "Neutral"
                s = val.strip()
                if not s:
                    return "Neutral"

                # Use the first token before any comma, e.g. "Sadness, Positive"
                base = s.split(",")[0].strip().title()

                if base in {"Positive", "Negative", "Neutral"}:
                    return base

                positive_like = {
                    "Inspiring",
                    "Hopeful",
                    "Uplifting",
                    "Optimistic",
                    "Encouraging",
                }
                negative_like = {
                    "Alarming",
                    "Critical",
                    "Sad",
                    "Angry",
                    "Anxious",
                    "Controversial",
                }
                neutral_like = {
                    "Analytical",
                    "Reflective",
                    "Mixed",
                    "Nostalgic",
                    "Informational",
                }

                if base in positive_like:
                    return "Positive"
                if base in negative_like:
                    return "Negative"
                if base in neutral_like:
                    return "Neutral"

                # Fallback bucket
                return "Neutral"

            sentiment_series = df["sentiment"].apply(canonicalize_sentiment)
            sentiment_counts = sentiment_series.value_counts()
            fig_pie = px.pie(
                sentiment_counts,
                values=sentiment_counts.values,
                names=sentiment_counts.index,
                title="Overall Sentiment",
                hole=0.4,
                template="plotly_dark",
            )
            st.plotly_chart(fig_pie, use_container_width=True)

    with c2:
        if "emotion" in df.columns:
            emotions = df["emotion"].dropna()
            if not emotions.empty:
                emotion_counts = emotions.value_counts().head(10)
                fig_em = px.bar(
                    x=emotion_counts.index,
                    y=emotion_counts.values,
                    title="Top Emotional Tones",
                    labels={"x": "Emotion", "y": "Count"},
                    template="plotly_dark",
                )
                st.plotly_chart(fig_em, use_container_width=True)

    # Topic Modeling
    st.subheader("Topic Landscape")
    all_topics = [topic for topics in df["topics"] if topics is not None for topic in topics]
    if all_topics:
        topic_counts = pd.Series(all_topics).value_counts().head(30).reset_index()
        topic_counts.columns = ["Topic", "Frequency"]

        fig_tree = px.treemap(
            topic_counts,
            path=["Topic"],
            values="Frequency",
            title="Top 30 Topics",
            template="plotly_dark",
        )
        st.plotly_chart(fig_tree, use_container_width=True)

def render_network(df):
    st.header("Network & Influence")

    if "people" not in df.columns:
        st.warning("No named entity data found. Please re-run enrichment script.")
        return

    c1, c2 = st.columns(2)

    with c1:
        st.subheader("People of Interest")
        all_people = [p for people in df["people"] if people is not None for p in people]
        if all_people:
            people_counts = pd.Series(all_people).value_counts().head(15).reset_index()
            people_counts.columns = ["Person", "Mentions"]
            st.dataframe(people_counts, use_container_width=True)

    with c2:
        st.subheader("Organizations & Companies")
        all_orgs = [o for orgs in df["orgs"] if orgs is not None for o in orgs]
        if all_orgs:
            org_counts = pd.Series(all_orgs).value_counts().head(15).reset_index()
            org_counts.columns = ["Organization", "Mentions"]
            st.dataframe(org_counts, use_container_width=True)

    st.markdown("---")
    c3, c4 = st.columns(2)

    with c3:
        if "locations" in df.columns:
            st.subheader("Locations")
            all_locs = [loc for locs in df["locations"] if locs is not None for loc in locs]
            if all_locs:
                loc_counts = pd.Series(all_locs).value_counts().head(15).reset_index()
                loc_counts.columns = ["Location", "Mentions"]
                st.dataframe(loc_counts, use_container_width=True)

    with c4:
        if "concepts" in df.columns:
            st.subheader("Concepts")
            def _titleize_concept(text: str) -> str:
                if not isinstance(text, str):
                    return text
                words = []
                for w in text.split():
                    if w.upper() in {"AI", "USA", "US", "EU", "UK"}:
                        words.append(w.upper())
                    else:
                        words.append(w.capitalize())
                return " ".join(words)

            all_concepts = [
                _titleize_concept(c)
                for cs in df["concepts"]
                if cs is not None
                for c in cs
            ]
            if all_concepts:
                concept_counts = pd.Series(all_concepts).value_counts().head(15).reset_index()
                concept_counts.columns = ["Concept", "Mentions"]
                st.dataframe(concept_counts, use_container_width=True)


def render_concept_explorer(df):
    st.header("Cluster Explorer")

    # Map UI label -> (column name, singular label)
    entity_map = {
        "Concepts": ("concepts", "Concept"),
        "Topics": ("topics", "Topic"),
        "People": ("people", "Person"),
        "Organizations": ("orgs", "Organization"),
        "Locations": ("locations", "Location"),
    }

    cluster_by = st.selectbox("Cluster articles by", list(entity_map.keys()), index=0)
    col_name, singular_label = entity_map[cluster_by]

    if col_name not in df.columns:
        st.warning(f"No {cluster_by.lower()} data found. Please re-run the enrichment script.")
        return

    # Helper to normalize concepts/locations capitalization
    def _titleize_concept(text: str) -> str:
        if not isinstance(text, str):
            return text
        words = []
        for w in text.split():
            if w.upper() in {"AI", "USA", "US", "EU", "UK"}:
                words.append(w.upper())
            else:
                words.append(w.capitalize())
        return " ".join(words)

    # Normalization function per entity type
    def normalize(value: str) -> str:
        if not isinstance(value, str):
            return value
        v = value.strip()
        if not v:
            return v
        if cluster_by in {"Concepts", "Locations"}:
            return _titleize_concept(v)
        if cluster_by == "Topics":
            return v.title()
        # People / Orgs – leave as-is except trimming
        return v

    # Flatten selected entity column
    all_values = [
        normalize(v)
        for seq in df[col_name]
        if seq is not None
        for v in seq
    ]

    if not all_values:
        st.info(f"No {cluster_by.lower()} have been detected yet. Try enriching more articles.")
        return

    counts = pd.Series(all_values).value_counts().reset_index()
    counts.columns = [singular_label, "Mentions"]

    c1, c2 = st.columns([1, 2])

    with c1:
        st.subheader(f"Top {cluster_by}")
        st.dataframe(counts.head(50), use_container_width=True, height=500)

        options = counts[singular_label].tolist()
        selected_value = st.selectbox(
            f"Select a {singular_label.lower()} to explore",
            options,
            index=0 if options else None,
        )

    with c2:
        st.subheader(f"Articles for Selected {singular_label}")

        if not selected_value:
            st.info("Select a value from the dropdown to see related articles.")
            return

        # Global expand/collapse controls
        ctrl_col, _ = st.columns([1, 3])
        with ctrl_col:
            if st.button("Expand all", key="cluster_expand_all_btn"):
                st.session_state["cluster_expand_all"] = True
            if st.button("Collapse all", key="cluster_collapse_all_btn"):
                st.session_state["cluster_expand_all"] = False

        expand_all = st.session_state.get("cluster_expand_all", False)

        # Filter articles that contain this entity (normalized)
        def has_value(row):
            seq = row.get(col_name)
            if seq is None:
                return False
            return any(normalize(v) == selected_value for v in seq)

        entity_articles = df[df.apply(has_value, axis=1)].sort_values(
            by="date_saved", ascending=False
        )

        st.caption(f"Found {len(entity_articles)} articles for **{selected_value}**.")

        for _, row in entity_articles.head(100).iterrows():
            title = row.get("title", "Untitled")
            date_str = (
                row["date_saved"].date().isoformat()
                if hasattr(row.get("date_saved"), "date")
                else str(row.get("date_saved", ""))
            )
            with st.expander(f"{date_str} — {title}", expanded=expand_all):
                c_main, c_meta = st.columns([3, 1])
                with c_main:
                    summary = row.get("summary")
                    if summary:
                        st.info(f"**TL;DR:** {summary}")
                    snippet = row.get("content_snippet")
                    if snippet:
                        st.caption(f"Preview: {snippet[:300]}...")

                with c_meta:
                    st.markdown(f"**Author:** {row.get('author', 'Unknown')}")
                    if row.get("emotion"):
                        st.markdown(f"**Tone:** {row['emotion']}")
                    if row.get("url"):
                        st.markdown(f"[Read Original]({row['url']})")

                # Show related tags for more context
                tags = []
                topics = row.get("topics")
                if topics is not None and len(topics) > 0:
                    tags.extend(topics)
                people = row.get("people")
                if people is not None and len(people) > 0:
                    tags.extend(people)
                locations = row.get("locations")
                if locations is not None and len(locations) > 0:
                    tags.extend(locations)
                concepts = row.get("concepts")
                if concepts is not None and len(concepts) > 0:
                    tags.extend([_titleize_concept(c) for c in concepts])

                if tags:
                    # De-duplicate while preserving order
                    uniq = list(dict.fromkeys(tags))
                    st.write(
                        "Tags: " + ", ".join([f"`{t}`" for t in uniq[:15]])
                    )

def render_explorer(df):
    st.header("Archive Explorer")

    search_term = st.text_input("Search archive...", placeholder="Type keywords, topics, entities, or emotions...")

    results = df
    if search_term:
        # Robust search across multiple fields including list columns
        def make_search_blob(row):
            parts = [
                str(row.get("title", "")),
                str(row.get("author", "")),
                str(row.get("summary", "")),
                str(row.get("emotion", "")),
            ]

            topics = row.get("topics")
            if topics is not None and len(topics) > 0:
                parts.extend(topics)

            people = row.get("people")
            if people is not None and len(people) > 0:
                parts.extend(people)

            locations = row.get("locations")
            if locations is not None and len(locations) > 0:
                parts.extend(locations)

            concepts = row.get("concepts")
            if concepts is not None and len(concepts) > 0:
                parts.extend(concepts)

            return " ".join(parts).lower()

        mask = df.apply(lambda x: search_term.lower() in make_search_blob(x), axis=1)
        results = df[mask]

    st.write(f"Showing {len(results)} articles")

    for _, row in results.head(50).iterrows():
        with st.expander(f"{row['date_saved']} - {row['title']}"):
            c1, c2 = st.columns([3, 1])
            with c1:
                if row.get("summary"):
                    st.info(f"**TL;DR:** {row['summary']}")
                else:
                    st.text("No summary available.")

                if row.get("content_snippet"):
                    st.caption(f"Preview: {row['content_snippet'][:300]}...")

            with c2:
                st.markdown(f"**Author:** {row['author']}")
                if row.get("emotion"):
                    st.markdown(f"**Tone:** {row['emotion']}")
                st.markdown(f"[Read Original]({row['url']})")

            # Tags
            tags = []
            topics = row.get("topics")
            if topics is not None and len(topics) > 0:
                tags.extend(topics)

            people = row.get("people")
            if people is not None and len(people) > 0:
                tags.extend(people)

            locations = row.get("locations")
            if locations is not None and len(locations) > 0:
                tags.extend(locations)

            concepts = row.get("concepts")
            if concepts is not None and len(concepts) > 0:
                tags.extend(concepts)

            if tags:
                st.write("Tags: " + ", ".join([f"`{t}`" for t in tags[:10]]))

if __name__ == "__main__":
    main()


