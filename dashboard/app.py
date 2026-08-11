import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from datetime import datetime, timedelta
import frontmatter

# Config
st.set_page_config(page_title="Article Archive Analytics", layout="wide", initial_sidebar_state="expanded")
DATA_DIR = Path(__file__).parent.parent / "data"
INDEX_PATH = DATA_DIR / "archive_index.parquet"
REVIEW_HISTORY_PATH = DATA_DIR / "review_history.parquet"

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

def load_data():
    """Load data without caching - always fresh from disk."""
    if not INDEX_PATH.exists():
        st.error("Index file not found. Please run `scripts/build_index.py`.")
        return pd.DataFrame()

    # Force clear any existing cache
    if hasattr(load_data, '_cache'):
        delattr(load_data, '_cache')

    return pd.read_parquet(INDEX_PATH)

# Reading eras, in the order they should appear. The archive spans three
# ingestion sources and the dashboard reads better when they are named the way
# Adam thinks about them rather than by their frontmatter value.
ERA_LABELS = {
    "instapaper": "Instapaper",
    "matter": "Matter",
    "unknown": "Unknown",
}
ERA_ORDER = ["Legacy files", "Instapaper", "Matter", "Unknown"]


def derive_era(df):
    """Label each row with the reading era it came from.

    Tolerates an index built before the `source` column existed: without it,
    every row is attributed by whether it has an instapaper_id, which is exactly
    what build_index.py now does at parse time.
    """
    if "source" in df.columns:
        source = df["source"].fillna("unknown").astype(str)
    elif "instapaper_id" in df.columns:
        source = df["instapaper_id"].notna().map({True: "instapaper", False: "unknown"})
    else:
        return pd.Series(["Unknown"] * len(df), index=df.index)

    return source.map(
        lambda value: "Legacy files" if value.startswith("legacy")
        else ERA_LABELS.get(value, value.title() if value else "Unknown")
    )


def review_id(article):
    """A stable identity for the spaced-review system.

    Reviews were originally keyed on instapaper_id, which only the Instapaper
    era has -- the legacy import (about 10,560 rows) and now Matter both leave
    it null, so their review records were written against NaN and could never be
    matched back to an article. Falling back to matter_id and then file_path
    fixes both. Instapaper articles keep returning instapaper_id, so review
    history recorded before this change still matches.
    """
    value = article.get("instapaper_id")
    if value is not None and not (isinstance(value, float) and pd.isna(value)):
        return value
    for key in ("matter_id", "file_path"):
        alternative = article.get(key)
        if isinstance(alternative, str) and alternative:
            return alternative
    return None


def review_id_series(df):
    """review_id() for every row, for matching against saved review history."""
    if df.empty:
        return pd.Series(dtype=object, index=df.index)
    return df.apply(review_id, axis=1)


def load_review_history():
    """Load review history or create empty dataframe."""
    if REVIEW_HISTORY_PATH.exists():
        return pd.read_parquet(REVIEW_HISTORY_PATH)
    else:
        return pd.DataFrame(columns=[
            "article_id", "last_reviewed", "next_review",
            "ease_factor", "interval_days", "review_count"
        ])

def save_review_history(df_history):
    """Save review history to parquet."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df_history.to_parquet(REVIEW_HISTORY_PATH, index=False)

def calculate_next_review(ease_factor, interval_days, quality):
    """
    SM-2 Spaced Repetition Algorithm

    quality: 0 (Hard), 1 (Good), 2 (Easy)
    Returns: (new_ease_factor, new_interval_days)
    """
    # Update ease factor
    new_ease = ease_factor + (0.1 - (2 - quality) * (0.08 + (2 - quality) * 0.02))
    new_ease = max(1.3, new_ease)  # Minimum ease factor

    # Calculate new interval
    if quality == 0:  # Hard - review sooner
        new_interval = max(1, interval_days * 0.5)
    elif quality == 1:  # Good - standard progression
        new_interval = interval_days * new_ease
    else:  # Easy - longer interval
        new_interval = interval_days * new_ease * 1.3

    # First review intervals
    if interval_days == 0:
        if quality == 0:
            new_interval = 1  # Tomorrow
        elif quality == 1:
            new_interval = 3  # 3 days
        else:
            new_interval = 7  # 1 week

    return new_ease, int(new_interval)

def update_review_record(article_id, quality, df_history):
    """Update or create review record for an article."""
    now = datetime.now()

    if article_id in df_history["article_id"].values:
        # Update existing record
        idx = df_history[df_history["article_id"] == article_id].index[0]
        current_ease = df_history.loc[idx, "ease_factor"]
        current_interval = df_history.loc[idx, "interval_days"]

        new_ease, new_interval = calculate_next_review(current_ease, current_interval, quality)

        df_history.loc[idx, "last_reviewed"] = now
        df_history.loc[idx, "next_review"] = now + timedelta(days=new_interval)
        df_history.loc[idx, "ease_factor"] = new_ease
        df_history.loc[idx, "interval_days"] = new_interval
        df_history.loc[idx, "review_count"] += 1
    else:
        # Create new record
        new_ease, new_interval = calculate_next_review(2.5, 0, quality)

        new_record = pd.DataFrame([{
            "article_id": article_id,
            "last_reviewed": now,
            "next_review": now + timedelta(days=new_interval),
            "ease_factor": new_ease,
            "interval_days": new_interval,
            "review_count": 1
        }])
        df_history = pd.concat([df_history, new_record], ignore_index=True)

    return df_history

def main():
    st.title("📚 Article Archive Analytics")

    # Add reload button in sidebar
    if st.sidebar.button("🔄 Force Reload Data", help="Click if data seems outdated"):
        st.cache_data.clear()
        st.rerun()

    df = load_data()
    if df.empty:
        return

    # Composite date_read column: prefer date_archived (when article was read)
    # with fallback to date_saved (when clipped) for articles without archive date
    if "date_archived" in df.columns:
        df["date_read"] = df["date_archived"].fillna(df["date_saved"])
    else:
        df["date_read"] = df["date_saved"]

    df["era"] = derive_era(df)

    # Debug info
    st.sidebar.caption(f"📊 Loaded: {len(df)} articles")
    st.sidebar.caption(f"📅 Date range: {df['date_read'].min().date()} to {df['date_read'].max().date()}")

    # Sidebar Navigation
    page = st.sidebar.radio(
        "Navigation",
        [
            "The Quantified Reader",
            "Content Intelligence",
            "Network & Entities",
            "Concept Explorer",
            "Archive Explorer",
            "Trends Over Time",
            "Heatmap Analysis",
            "Spaced Review",
        ],
    )

    # Global Sidebar Filters
    st.sidebar.markdown("---")
    st.sidebar.subheader("Filter Archive")

    # Date Range - use wide constraints to avoid cache issues
    from datetime import date

    # Set very wide date bounds (not constrained by potentially cached data)
    absolute_min_date = date(1950, 1, 1)
    absolute_max_date = date(2030, 12, 31)

    # Get actual dates from current data
    actual_min_date = df["date_read"].min().date() if not df.empty else absolute_min_date
    actual_max_date = df["date_read"].max().date() if not df.empty else absolute_max_date

    date_range = st.sidebar.date_input(
        "Date Range",
        value=(actual_min_date, actual_max_date),
        min_value=absolute_min_date,  # Allow selecting back to 1950
        max_value=absolute_max_date,  # Allow selecting up to 2030
    )

    # Filter Data based on Date
    if len(date_range) == 2:
        mask = (df["date_read"].dt.date >= date_range[0]) & (df["date_read"].dt.date <= date_range[1])
        df_filtered = df.loc[mask]
    else:
        df_filtered = df

    # Reading era. Only offered when the archive actually holds more than one,
    # so a single-source archive does not grow a filter that does nothing.
    present_eras = [era for era in ERA_ORDER if era in set(df["era"])]
    present_eras += sorted(set(df["era"]) - set(ERA_ORDER))
    if len(present_eras) > 1:
        chosen_eras = st.sidebar.multiselect("Reading Era", present_eras, default=present_eras)
        if chosen_eras:
            df_filtered = df_filtered[df_filtered["era"].isin(chosen_eras)]

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
    elif page == "Trends Over Time":
        render_trends(df_filtered)
    elif page == "Heatmap Analysis":
        render_heatmaps(df_filtered)
    elif page == "Spaced Review":
        render_review(df_filtered)

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

    # Where the corpus came from. One continuous reading history, three
    # ingestion eras - worth showing, because the shape of what the archive
    # knows about an article differs by era.
    if "era" in df.columns and df["era"].nunique() > 1:
        counts = df["era"].value_counts()
        ordered = [era for era in ERA_ORDER if era in counts.index]
        ordered += [era for era in counts.index if era not in ordered]
        era_cols = st.columns(len(ordered))
        for column, era in zip(era_cols, ordered):
            share = counts[era] / total_articles * 100 if total_articles else 0
            column.metric(era, f"{counts[era]:,}", f"{share:.0f}% of archive")

    # Reading Achievements - Contextualize the word count
    st.markdown("---")

    # Achievement Badges based on word count milestones
    milestones = [
        (100000000, "🏆 Library of Congress", "You've read more than most libraries contain!"),
        (50000000, "📚 Encyclopedia Master", "You've exceeded major encyclopedias!"),
        (25000000, "🎓 PhD x 100", "Equivalent to reading 100 doctoral dissertations"),
        (10000000, "📖 Literature Scholar", "You've read more than 100 novels worth!"),
        (5000000, "🌟 Bookworm Elite", "You've consumed massive amounts of knowledge"),
        (2500000, "📚 Avid Reader", "You've read dozens of books worth"),
        (1000000, "🎯 First Million!", "You've crossed the million-word milestone"),
        (500000, "📖 Novel Reader", "Equivalent to 5-6 full novels"),
        (100000, "🌱 Getting Started", "Building your knowledge base"),
    ]

    # Find current achievement level
    current_achievement = None
    for threshold, badge, description in milestones:
        if total_words >= threshold:
            current_achievement = (badge, description, threshold)
            break

    if current_achievement:
        badge, description, threshold = current_achievement
        st.subheader(f"📚 Reading Achievements - {badge}")
        col_badge_1, col_badge_2 = st.columns([2, 1])
        with col_badge_1:
            st.info(f"**{description}**")
        with col_badge_2:
            # Show next milestone
            next_milestone = None
            for t, b, d in milestones:
                if t > threshold:
                    next_milestone = (b, t)
            if next_milestone:
                next_badge, next_threshold = next_milestone
                words_to_go = next_threshold - total_words
                st.metric(
                    "Next Milestone",
                    next_badge,
                    f"{words_to_go/1000000:.1f}M words to go"
                )
    else:
        st.subheader("📚 Reading Achievements - Keep Reading!")

    # Calculate comparisons
    novels_equivalent = total_words / 90000  # Average novel ~90k words
    harry_potter_series = total_words / 1084170  # All 7 books
    war_and_peace = total_words / 587287
    bibles = total_words / 783137  # King James Version
    phd_theses = total_words / 80000  # Average PhD dissertation
    wiki_articles = total_words / 600  # Average Wikipedia article
    printed_pages = total_words / 300  # ~300 words per page
    continuous_days = hours_read / 24

    col_a, col_b, col_c = st.columns(3)

    with col_a:
        st.markdown("### 📖 Literary Comparisons")

        # Show relevant comparisons based on word count
        comparisons = []
        comparisons.append(f"- **{novels_equivalent:.0f} novels** (avg 90k words)")

        if total_words >= 1000000:
            comparisons.append(f"- **{harry_potter_series:.1f}x** the entire Harry Potter series")
        if total_words >= 500000:
            comparisons.append(f"- **{war_and_peace:.1f}x** War and Peace")
        if total_words >= 700000:
            comparisons.append(f"- **{bibles:.1f}x** the King James Bible")

        # Add aspirational comparisons for smaller archives
        if total_words < 1000000:
            hp_progress = (total_words / 1084170) * 100
            comparisons.append(f"- **{hp_progress:.0f}%** of Harry Potter series")
        if total_words < 500000:
            wp_progress = (total_words / 587287) * 100
            comparisons.append(f"- **{wp_progress:.0f}%** of War and Peace")

        st.markdown("\n".join(comparisons))

    with col_b:
        st.markdown("### ⏱️ Time Investment")
        st.markdown(f"""
        At average reading speed (250 wpm):
        - **{hours_read:,.0f} hours** of reading
        - **{continuous_days:.1f} days** of continuous reading
        - **{hours_read/52:.0f} hours/week** for a year
        """)

        # Calculate reading pace
        date_span = (df["date_read"].max() - df["date_read"].min()).days
        if date_span > 0:
            years_span = date_span / 365.25
            words_per_day = total_words / date_span
            st.markdown(f"- **{words_per_day:,.0f} words/day** average pace")

    with col_c:
        st.markdown("### 📏 Physical Scale")
        st.markdown(f"""
        If printed as a book:
        - **{printed_pages:,.0f} pages** (standard formatting)
        - **{printed_pages/250:.0f} books** (avg 250 pages each)
        - Stack height: **{printed_pages * 0.004:.1f} inches** (~0.004" per page)
        """)
        st.markdown(f"""
        Academic equivalents:
        - **{phd_theses:.0f} PhD dissertations**
        - **{wiki_articles:,.0f} Wikipedia articles**
        """)

    # Visual comparison chart - dynamically select relevant works
    st.markdown("---")
    st.subheader("📊 Visual Comparison to Famous Works")

    # Build comparison list based on word count range
    all_works = [
        ("Your Archive", total_words, "You"),
        ("Encyclopedia Britannica", 44000000, "Reference"),
        ("Complete Works of Shakespeare", 884421, "Classic"),
        ("Harry Potter Series", 1084170, "Modern"),
        ("Lord of the Rings Trilogy", 481103, "Classic"),
        ("War and Peace", 587287, "Classic"),
        ("The Bible", 783137, "Religious"),
        ("Moby Dick", 206052, "Classic"),
        ("Great Gatsby", 47094, "Classic"),
        ("Ulysses by Joyce", 265222, "Classic"),
    ]

    # Select works that make sense for comparison (within 10x range)
    relevant_works = [("Your Archive", total_words, "You")]
    for work, words, category in all_works:
        if work != "Your Archive":
            # Include if within reasonable comparison range
            if words <= total_words * 100 and words >= total_words * 0.01:
                relevant_works.append((work, words, category))

    # Always include at least a few comparison points
    if len(relevant_works) < 5:
        # Add some from the full list
        sorted_works = sorted(all_works, key=lambda x: abs(x[1] - total_words))
        for work in sorted_works[:7]:
            if work not in relevant_works and work[0] != "Your Archive":
                relevant_works.append(work)

    famous_works = pd.DataFrame(relevant_works, columns=["Work", "Words", "Category"])

    fig = px.bar(
        famous_works.sort_values("Words"),
        x="Words",
        y="Work",
        orientation="h",
        color="Category",
        title="How Your Archive Compares to Famous Works",
        labels={"Words": "Total Words", "Work": ""},
        template="plotly_dark",
        color_discrete_map={
            "You": "#FF4B4B",
            "Reference": "#888888",
            "Classic": "#4B9BFF",
            "Modern": "#9B4BFF",
            "Religious": "#FFB84B"
        }
    )
    fig.update_layout(showlegend=True, height=400)
    st.plotly_chart(fig, use_container_width=True)

    # Timeline
    st.subheader("Reading Activity Over Time")
    st.caption("📖 Shows when you read articles (based on archive date, with save date as fallback)")
    # Resample by month-end
    timeline = df.set_index("date_read").resample("ME").size().reset_index(name="count")

    fig = px.bar(
        timeline,
        x="date_read",
        y="count",
        title="Articles Read per Month",
        labels={"date_read": "Date", "count": "Articles Read"},
        template="plotly_dark",
    )
    fig.update_traces(marker_color="#FF4B4B")
    st.plotly_chart(fig, use_container_width=True)

    # Reading Rhythms
    st.subheader("Reading Patterns")
    st.caption("⏰ Based on when articles were read (archive date, with save date as fallback)")
    c1, c2 = st.columns(2)

    with c1:
        # Day of Week Analysis
        df["day_of_week"] = df["date_read"].dt.day_name()
        days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        day_counts = df["day_of_week"].value_counts().reindex(days_order).reset_index()
        day_counts.columns = ["Day", "Count"]

        fig_day = px.bar(day_counts, x="Day", y="Count", title="Reading by Day of Week", template="plotly_dark")
        st.plotly_chart(fig_day, use_container_width=True)

    with c2:
        # Complexity over Time
        if "grade_level" in df.columns:
            complexity = df.set_index("date_read")["grade_level"].resample("ME").mean().reset_index()
            fig_comp = px.line(
                complexity,
                x="date_read",
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

    # Word Cloud for Concepts
    st.subheader("☁️ Concept Word Cloud")

    if "concepts" in df.columns:
        try:
            from wordcloud import WordCloud
            import matplotlib.pyplot as plt
            import io

            # Helper for concept normalization
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

            # Get all concepts
            all_concepts = []
            for concepts in df["concepts"]:
                if concepts is not None:
                    for c in concepts:
                        all_concepts.append(_titleize_concept(c))

            if all_concepts:
                # Create word cloud
                concept_freq = {}
                for concept in all_concepts:
                    concept_freq[concept] = concept_freq.get(concept, 0) + 1

                wordcloud = WordCloud(
                    width=1200,
                    height=400,
                    background_color='#0E1117',  # Match dark theme
                    colormap='viridis',
                    max_words=100,
                    relative_scaling=0.5,
                    min_font_size=10
                ).generate_from_frequencies(concept_freq)

                # Create matplotlib figure
                fig, ax = plt.subplots(figsize=(15, 5))
                ax.imshow(wordcloud, interpolation='bilinear')
                ax.axis('off')
                fig.patch.set_facecolor('#0E1117')

                # Display in streamlit
                st.pyplot(fig)
                plt.close()
            else:
                st.info("No concepts found in enrichment data.")
        except ImportError:
            st.warning("Word cloud library not installed. Run: pip install wordcloud matplotlib")

    # Bubble Chart - Concept Evolution (OPTIMIZED)
    st.markdown("---")
    st.subheader("🫧 Concept Evolution - When Did Ideas Emerge?")
    st.caption("Bubble chart showing when concepts first appeared, their total mentions, and prevalence")

    if "concepts" in df.columns:
        # Use cached/optimized approach
        with st.spinner("Generating bubble chart..."):
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

            # Filter articles with concepts and valid dates
            # Note: concepts are stored as numpy arrays from parquet
            df_with_concepts = df[
                df["concepts"].notna() &
                df["date_read"].notna() &
                df["concepts"].apply(lambda x: hasattr(x, '__len__') and len(x) > 0)
            ].copy()

            if not df_with_concepts.empty:
                # Build concept data more efficiently
                concept_data = []
                for _, row in df_with_concepts.iterrows():
                    date = row["date_read"]
                    for concept in row["concepts"]:
                        concept_data.append({
                            "concept": _titleize_concept(concept),
                            "date": date
                        })

                if concept_data:
                    concept_df = pd.DataFrame(concept_data)

                    # Vectorized aggregation (much faster)
                    concept_stats = concept_df.groupby("concept").agg(
                        first_mention=("date", "min"),
                        last_mention=("date", "max"),
                        total_mentions=("date", "count")
                    ).reset_index()

                    # Calculate years active
                    concept_stats["Years Active"] = (
                        (concept_stats["last_mention"] - concept_stats["first_mention"]).dt.days / 365.25
                    ).clip(lower=0.1)  # Min 0.1

                    # Rename columns
                    concept_stats = concept_stats.rename(columns={
                        "concept": "Concept",
                        "first_mention": "First Mentioned",
                        "total_mentions": "Total Mentions"
                    })

                    concept_stats["Article Count"] = concept_stats["Total Mentions"]

                    # Filter to concepts with 5+ mentions and top 50
                    bubble_df = concept_stats[concept_stats["Total Mentions"] >= 5].nlargest(50, "Total Mentions")

                    # Create bubble chart
                    fig = px.scatter(
                        bubble_df,
                        x="First Mentioned",
                        y="Total Mentions",
                        size="Article Count",
                        hover_data=["Concept", "Years Active"],
                        color="Years Active",
                        size_max=60,
                        title="Concept Timeline - When Ideas Emerged and How Long They Lasted",
                        labels={
                            "First Mentioned": "Year First Mentioned",
                            "Total Mentions": "Total Mentions Across Archive",
                            "Years Active": "Years Between First & Last Mention"
                        },
                        template="plotly_dark",
                        color_continuous_scale="viridis"
                    )

                    fig.update_layout(
                        height=600,
                        hovermode="closest",
                        xaxis_title="Year First Mentioned",
                        yaxis_title="Total Mentions"
                    )

                    # Add concept labels to bubbles
                    fig.update_traces(
                        text=bubble_df["Concept"],
                        textposition="top center",
                        textfont_size=8
                    )

                    st.plotly_chart(fig, use_container_width=True)

                    st.caption("💡 **Bubble size** = number of articles | **Color** = how long the concept stayed relevant | **Position** = when it first appeared")
                else:
                    st.info("No concept data available for bubble chart.")
            else:
                st.info("No articles with concepts found.")

    # Sentiment & Emotion
    st.markdown("---")
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

def render_trends(df):
    st.header("📈 Trends Over Time")
    st.caption("📖 Based on when articles were read (archive date, with save date as fallback)")

    # Check if we have enrichment data
    if "topics" not in df.columns or df["topics"].isna().all():
        st.warning("No AI enrichment data found. Please run enrichment scripts to enable trend analysis.")
        return

    # Date range info
    min_date = df["date_read"].min()
    max_date = df["date_read"].max()
    years_span = (max_date - min_date).days / 365.25

    st.info(f"📅 Reading period spans **{years_span:.1f} years** from {min_date.date()} to {max_date.date()}")

    # Helper function for concept normalization
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

    # Pre-populated Top 10 Charts
    st.markdown("---")
    st.subheader("🔥 Top Trends Over Time - Auto-Generated")
    st.caption("Most frequently mentioned entities across your archive, tracked over time")

    # Tabs for different entity types
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📰 Sources", "📍 Locations", "🏢 Organizations", "👥 People", "💡 Concepts", "📚 Topics"])

    # Helper function to extract domain from URL
    def extract_domain(url):
        """Extract domain from URL, handling various formats."""
        if not isinstance(url, str) or not url:
            return "Unknown"
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc or parsed.path
            # Remove www. prefix
            if domain.startswith('www.'):
                domain = domain[4:]
            # Remove port if present
            if ':' in domain:
                domain = domain.split(':')[0]
            return domain if domain else "Unknown"
        except:
            return "Unknown"

    # SOURCES TAB
    with tab1:
        st.subheader("📰 Top Publication Sources Over Time")
        st.caption("Tracking where your articles come from")

        # Extract domains from URLs
        df_with_sources = df[df["url"].notna()].copy()
        df_with_sources["source"] = df_with_sources["url"].apply(extract_domain)

        # Filter out "Unknown" sources
        df_with_sources = df_with_sources[df_with_sources["source"] != "Unknown"]

        if not df_with_sources.empty:
            # Get top 10 sources
            source_counts = df_with_sources["source"].value_counts()
            top_10_sources = source_counts.head(10).index.tolist()

            # Build time series for top 10 sources
            time_series_data = []

            for source in top_10_sources:
                source_df = df_with_sources[df_with_sources["source"] == source].copy()

                if source_df.empty:
                    continue

                # Resample by quarter (good balance for overview)
                source_df = source_df.set_index("date_read")
                resampled = source_df.resample("QE").size()

                for date, count in resampled.items():
                    time_series_data.append({
                        "Date": date,
                        "Source": source,
                        "Articles": count
                    })

            if time_series_data:
                ts_df = pd.DataFrame(time_series_data)

                # Create line chart
                fig = px.line(
                    ts_df,
                    x="Date",
                    y="Articles",
                    color="Source",
                    title="Top 10 Publication Sources - Articles Over Time (Quarterly)",
                    labels={"Articles": "Articles per Quarter", "Date": "Time Period"},
                    template="plotly_dark",
                    markers=True
                )

                fig.update_layout(
                    hovermode="x unified",
                    height=500,
                    legend=dict(
                        orientation="v",
                        yanchor="top",
                        y=1,
                        xanchor="left",
                        x=1.02
                    )
                )

                st.plotly_chart(fig, use_container_width=True)

                # Interactive legend tip
                st.info("💡 **Tip:** Click on any source in the legend to show/hide that line. Double-click to isolate a single source.")

                # Show the top 10 list with counts
                st.caption(f"**Top 10 Sources:** {', '.join([f'{src} ({source_counts[src]})' for src in top_10_sources])}")
            else:
                st.warning("No time series data available for sources.")
        else:
            st.info("No source data available. Make sure articles have valid URLs.")

    entity_configs = [
        ("locations", "Locations", "Location", tab2),
        ("orgs", "Organizations", "Organization", tab3),
        ("people", "People", "Person", tab4),
        ("concepts", "Concepts", "Concept", tab5),
        ("topics", "Topics", "Topic", tab6),
    ]

    for column_name, entity_label, singular_label, tab in entity_configs:
        with tab:
            if column_name not in df.columns:
                st.warning(f"No {entity_label.lower()} data found.")
                continue

            # Get all entities
            all_entities = []
            for entities in df[column_name]:
                if entities is not None:
                    for e in entities:
                        if column_name in ["concepts", "locations"]:
                            all_entities.append(_titleize_concept(e))
                        else:
                            all_entities.append(e)

            if not all_entities:
                st.info(f"No {entity_label.lower()} detected yet.")
                continue

            # Get top 10
            entity_counts = pd.Series(all_entities).value_counts()
            top_10 = entity_counts.head(10).index.tolist()

            # Build time series for top 10
            time_series_data = []

            for entity in top_10:
                # Filter articles mentioning this entity
                def has_entity(row):
                    entities = row.get(column_name)
                    if entities is None:
                        return False
                    if column_name in ["concepts", "locations"]:
                        return any(_titleize_concept(e) == entity for e in entities)
                    else:
                        return entity in entities

                entity_df = df[df.apply(has_entity, axis=1)].copy()

                if entity_df.empty:
                    continue

                # Resample by quarter (good balance for overview)
                entity_df = entity_df.set_index("date_read")
                resampled = entity_df.resample("QE").size()

                for date, count in resampled.items():
                    time_series_data.append({
                        "Date": date,
                        "Entity": entity,
                        "Mentions": count
                    })

            if time_series_data:
                ts_df = pd.DataFrame(time_series_data)

                # Create line chart
                fig = px.line(
                    ts_df,
                    x="Date",
                    y="Mentions",
                    color="Entity",
                    title=f"Top 10 {entity_label} - Mentions Over Time (Quarterly)",
                    labels={"Mentions": f"Articles per Quarter", "Date": "Time Period"},
                    template="plotly_dark",
                    markers=True
                )

                fig.update_layout(
                    hovermode="x unified",
                    height=500,
                    legend=dict(
                        orientation="v",
                        yanchor="top",
                        y=1,
                        xanchor="left",
                        x=1.02
                    )
                )

                st.plotly_chart(fig, use_container_width=True)

                # Interactive legend tip
                st.info("💡 **Tip:** Click on any item in the legend to show/hide that line. Double-click to isolate a single entity.")

                # Show the top 10 list
                st.caption(f"**Top 10 {entity_label}:** {', '.join(top_10)}")
            else:
                st.warning(f"No time series data available for {entity_label.lower()}.")

    # Manual selection section
    st.markdown("---")
    st.subheader("🔍 Custom Entity Tracking")

    col1, col2 = st.columns([1, 2])

    with col1:
        entity_type = st.selectbox(
            "Entity Type",
            ["Locations", "Organizations", "People", "Concepts", "Topics"],
            index=0
        )

        time_granularity = st.selectbox(
            "Time Granularity",
            ["Month", "Quarter", "Year"],
            index=1
        )

    with col2:
        comparison_mode = st.checkbox("Comparison Mode (track multiple entities)", value=False)

        if comparison_mode:
            max_entities = st.slider("Number of entities to compare", 2, 10, 3)
        else:
            max_entities = 1

    # Map entity types to dataframe columns
    entity_map = {
        "Locations": "locations",
        "Organizations": "orgs",
        "People": "people",
        "Concepts": "concepts",
        "Topics": "topics"
    }

    column_name = entity_map[entity_type]

    if column_name not in df.columns:
        st.warning(f"No {entity_type.lower()} data found. Please run enrichment script.")
        return

    # Helper to normalize concepts/locations
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

    # Flatten and count all entities
    all_entities = []
    for entities in df[column_name]:
        if entities is not None:
            for e in entities:
                if entity_type in ["Concepts", "Locations"]:
                    all_entities.append(_titleize_concept(e))
                else:
                    all_entities.append(e)

    if not all_entities:
        st.info(f"No {entity_type.lower()} have been detected yet.")
        return

    entity_counts = pd.Series(all_entities).value_counts()

    # Entity selection
    st.markdown("---")
    st.subheader(f"Select {entity_type} to Track")

    col_list, col_chart = st.columns([1, 2])

    with col_list:
        top_entities = entity_counts.head(50).reset_index()
        top_entities.columns = [entity_type, "Total Mentions"]
        st.dataframe(top_entities, use_container_width=True, height=400)

        # Multi-select for entities
        if comparison_mode:
            selected_entities = st.multiselect(
                f"Select up to {max_entities} {entity_type.lower()} to compare",
                entity_counts.index.tolist(),
                default=entity_counts.index[:min(3, max_entities)].tolist()
            )[:max_entities]
        else:
            selected_entity = st.selectbox(
                f"Select a {entity_type[:-1].lower()}",
                entity_counts.index.tolist(),
                index=0
            )
            selected_entities = [selected_entity] if selected_entity else []

    with col_chart:
        if not selected_entities:
            st.info("Select an entity from the dropdown to see trends over time.")
            return

        st.subheader(f"Trend Over Time")

        # Build time series data for selected entities
        time_series_data = []

        for entity in selected_entities:
            # Filter articles mentioning this entity
            def has_entity(row):
                entities = row.get(column_name)
                if entities is None:
                    return False
                # Normalize for comparison
                if entity_type in ["Concepts", "Locations"]:
                    return any(_titleize_concept(e) == entity for e in entities)
                else:
                    return entity in entities

            entity_df = df[df.apply(has_entity, axis=1)].copy()

            if entity_df.empty:
                continue

            # Resample by time granularity
            entity_df = entity_df.set_index("date_read")

            if time_granularity == "Month":
                resampled = entity_df.resample("ME").size()
            elif time_granularity == "Quarter":
                resampled = entity_df.resample("QE").size()
            else:  # Year
                resampled = entity_df.resample("YE").size()

            # Convert to dataframe
            for date, count in resampled.items():
                time_series_data.append({
                    "Date": date,
                    "Entity": entity,
                    "Mentions": count
                })

        if not time_series_data:
            st.warning("No data found for selected entities.")
            return

        ts_df = pd.DataFrame(time_series_data)

        # Create line chart
        fig = px.line(
            ts_df,
            x="Date",
            y="Mentions",
            color="Entity" if comparison_mode else None,
            title=f"{entity_type} Mentions Over Time ({time_granularity}ly)",
            labels={"Mentions": f"Articles Mentioning {entity_type[:-1]}", "Date": "Time Period"},
            template="plotly_dark",
            markers=True
        )

        fig.update_layout(
            hovermode="x unified",
            height=500
        )

        st.plotly_chart(fig, use_container_width=True)

    # Additional insights
    st.markdown("---")
    st.subheader("📊 Temporal Insights")

    col_insights1, col_insights2, col_insights3 = st.columns(3)

    with col_insights1:
        st.markdown("### 🔥 Hottest Right Now")
        st.caption("Most mentioned in recent 6 months")

        # Get recent articles
        six_months_ago = max_date - pd.Timedelta(days=180)
        recent_df = df[df["date_read"] >= six_months_ago]

        recent_entities = []
        for entities in recent_df[column_name]:
            if entities is not None:
                for e in entities:
                    if entity_type in ["Concepts", "Locations"]:
                        recent_entities.append(_titleize_concept(e))
                    else:
                        recent_entities.append(e)

        if recent_entities:
            recent_top = pd.Series(recent_entities).value_counts().head(10)
            for entity, count in recent_top.items():
                st.write(f"• **{entity}** ({count})")
        else:
            st.write("No recent data")

    with col_insights2:
        st.markdown("### 📈 Emerging Trends")
        st.caption("Growing mentions over time")

        # Compare first vs second half of archive
        midpoint = min_date + (max_date - min_date) / 2
        first_half = df[df["date_read"] < midpoint]
        second_half = df[df["date_read"] >= midpoint]

        def get_entity_counts(subset_df):
            entities_list = []
            for entities in subset_df[column_name]:
                if entities is not None:
                    for e in entities:
                        if entity_type in ["Concepts", "Locations"]:
                            entities_list.append(_titleize_concept(e))
                        else:
                            entities_list.append(e)
            return pd.Series(entities_list).value_counts() if entities_list else pd.Series()

        first_counts = get_entity_counts(first_half)
        second_counts = get_entity_counts(second_half)

        # Find entities that grew
        emerging = []
        for entity in second_counts.index:
            first_val = first_counts.get(entity, 0)
            second_val = second_counts.get(entity, 0)
            if second_val > first_val and second_val >= 5:  # At least 5 mentions
                growth = ((second_val - first_val) / max(first_val, 1)) * 100
                emerging.append((entity, growth, second_val))

        emerging.sort(key=lambda x: x[1], reverse=True)

        for entity, growth, count in emerging[:10]:
            st.write(f"• **{entity}** (+{growth:.0f}% growth)")

    with col_insights3:
        st.markdown("### 📉 Declining Trends")
        st.caption("Fading mentions over time")

        # Find entities that declined
        declining = []
        for entity in first_counts.index:
            first_val = first_counts.get(entity, 0)
            second_val = second_counts.get(entity, 0)
            if first_val > second_val and first_val >= 5:  # Was at least 5 mentions
                decline = ((first_val - second_val) / first_val) * 100
                declining.append((entity, decline, first_val))

        declining.sort(key=lambda x: x[1], reverse=True)

        for entity, decline, count in declining[:10]:
            st.write(f"• **{entity}** (-{decline:.0f}% decline)")

def render_heatmaps(df):
    st.header("🗺️ Heatmap Analysis")

    # Check if we have enrichment data
    if "topics" not in df.columns or df["topics"].isna().all():
        st.warning("No AI enrichment data found. Please run enrichment scripts to enable heatmap analysis.")
        return

    # Date range info
    min_date = df["date_read"].min()
    max_date = df["date_read"].max()
    years_span = (max_date - min_date).days / 365.25

    st.info(f"📅 Reading history spans **{years_span:.1f} years** from {min_date.date()} to {max_date.date()}")

    # Helper for concept normalization
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

    # HEATMAP 1: Topic x Time
    st.subheader("📚 Topic Activity Over Time")
    st.caption("Shows which topics were hot in different time periods")

    if "topics" in df.columns:
        # Get top 20 topics
        all_topics = [topic for topics in df["topics"] if topics is not None for topic in topics]
        if all_topics:
            topic_counts = pd.Series(all_topics).value_counts()
            top_20_topics = topic_counts.head(20).index.tolist()

            # Build heatmap data
            heatmap_data = []

            for topic in top_20_topics:
                # Filter articles with this topic
                topic_df = df[df["topics"].apply(lambda x: x is not None and topic in x)].copy()

                if not topic_df.empty:
                    topic_df = topic_df.set_index("date_read")
                    # Resample by year for cleaner heatmap
                    yearly = topic_df.resample("YE").size()

                    for date, count in yearly.items():
                        heatmap_data.append({
                            "Topic": topic,
                            "Year": date.year,
                            "Mentions": count
                        })

            if heatmap_data:
                heatmap_df = pd.DataFrame(heatmap_data)

                # Pivot for heatmap
                pivot = heatmap_df.pivot(index="Topic", columns="Year", values="Mentions").fillna(0)

                # Create heatmap
                fig = px.imshow(
                    pivot,
                    labels=dict(x="Year", y="Topic", color="Articles"),
                    x=pivot.columns,
                    y=pivot.index,
                    color_continuous_scale="YlOrRd",
                    aspect="auto",
                    title="Topic Heatmap - When Were Topics Most Popular?",
                    template="plotly_dark"
                )

                fig.update_layout(height=600)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Not enough data for topic heatmap.")
        else:
            st.info("No topics found.")

    # HEATMAP 2: Geographic Focus Over Time
    st.markdown("---")
    st.subheader("🌍 Geographic Focus Over Time")
    st.caption("See how your reading shifted geographically across decades")

    if "locations" in df.columns:
        # Get top 15 locations
        all_locations = [
            _titleize_concept(loc) for locs in df["locations"]
            if locs is not None for loc in locs
        ]

        if all_locations:
            location_counts = pd.Series(all_locations).value_counts()
            top_15_locations = location_counts.head(15).index.tolist()

            # Build heatmap data
            heatmap_data = []

            for location in top_15_locations:
                # Filter articles with this location
                loc_df = df[df["locations"].apply(
                    lambda x: x is not None and any(_titleize_concept(l) == location for l in x)
                )].copy()

                if not loc_df.empty:
                    loc_df = loc_df.set_index("date_read")
                    # Resample by year
                    yearly = loc_df.resample("YE").size()

                    for date, count in yearly.items():
                        heatmap_data.append({
                            "Location": location,
                            "Year": date.year,
                            "Mentions": count
                        })

            if heatmap_data:
                heatmap_df = pd.DataFrame(heatmap_data)

                # Pivot for heatmap
                pivot = heatmap_df.pivot(index="Location", columns="Year", values="Mentions").fillna(0)

                # Create heatmap
                fig = px.imshow(
                    pivot,
                    labels=dict(x="Year", y="Location", color="Articles"),
                    x=pivot.columns,
                    y=pivot.index,
                    color_continuous_scale="Blues",
                    aspect="auto",
                    title="Geographic Focus - Where Were Your Articles Coming From?",
                    template="plotly_dark"
                )

                fig.update_layout(height=500)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Not enough data for geographic heatmap.")
        else:
            st.info("No locations found.")

    # HEATMAP 3: Sentiment Distribution Over Time by Topic
    st.markdown("---")
    st.subheader("😊 Sentiment Evolution by Topic")
    st.caption("Were certain topics more positive or negative in different years?")

    if "topics" in df.columns and "sentiment" in df.columns:
        # Get top 15 topics
        all_topics = [topic for topics in df["topics"] if topics is not None for topic in topics]

        if all_topics:
            topic_counts = pd.Series(all_topics).value_counts()
            top_15_topics = topic_counts.head(15).index.tolist()

            # Map sentiment to numeric
            def sentiment_to_numeric(s):
                s = str(s).lower().strip()
                if 'positive' in s:
                    return 1
                elif 'negative' in s:
                    return -1
                else:
                    return 0

            # Build heatmap data
            heatmap_data = []

            for topic in top_15_topics:
                # Filter articles with this topic
                topic_df = df[df["topics"].apply(lambda x: x is not None and topic in x)].copy()

                if not topic_df.empty:
                    topic_df = topic_df.set_index("date_read")

                    # Group by year and calculate average sentiment
                    for year in topic_df.index.year.unique():
                        year_data = topic_df[topic_df.index.year == year]
                        sentiments = year_data["sentiment"].apply(sentiment_to_numeric)
                        avg_sentiment = sentiments.mean()

                        heatmap_data.append({
                            "Topic": topic,
                            "Year": year,
                            "Avg Sentiment": avg_sentiment
                        })

            if heatmap_data:
                heatmap_df = pd.DataFrame(heatmap_data)

                # Pivot for heatmap
                pivot = heatmap_df.pivot(index="Topic", columns="Year", values="Avg Sentiment").fillna(0)

                # Create heatmap with diverging colorscale
                fig = px.imshow(
                    pivot,
                    labels=dict(x="Year", y="Topic", color="Sentiment"),
                    x=pivot.columns,
                    y=pivot.index,
                    color_continuous_scale="RdYlGn",  # Red (negative) to Green (positive)
                    color_continuous_midpoint=0,
                    aspect="auto",
                    title="Sentiment Heatmap - How Did Topic Sentiment Change Over Time?",
                    template="plotly_dark",
                    zmin=-1,
                    zmax=1
                )

                fig.update_layout(height=500)
                st.plotly_chart(fig, use_container_width=True)

                st.caption("🟢 Green = Positive sentiment | 🟡 Yellow = Neutral | 🔴 Red = Negative sentiment")
            else:
                st.info("Not enough data for sentiment heatmap.")
        else:
            st.info("No topics found.")
    else:
        st.info("Sentiment data not available.")

def render_review(df):
    st.header("🧠 Spaced Review - Remember What You've Read")

    # Check if we have enrichment data
    if "summary" not in df.columns or df["summary"].isna().all():
        st.warning("No AI summaries found. Please run `scripts/enrich_archive.py` to generate summaries for spaced review.")
        return

    # Load review history
    df_history = load_review_history()

    # Initialize session state
    if "review_mode" not in st.session_state:
        st.session_state.review_mode = "due"
    if "review_deck" not in st.session_state:
        st.session_state.review_deck = []
    if "review_index" not in st.session_state:
        st.session_state.review_index = 0
    if "session_complete" not in st.session_state:
        st.session_state.session_complete = False

    # Mode selection
    col1, col2 = st.columns([1, 3])
    with col1:
        mode = st.radio(
            "Review Mode",
            ["Due for Review", "Browse by Topic"],
            key="review_mode_radio",
            index=0 if st.session_state.review_mode == "due" else 1
        )

        if mode == "Due for Review":
            st.session_state.review_mode = "due"
        else:
            st.session_state.review_mode = "topic"

    # Stats sidebar
    with col2:
        now = datetime.now()
        due_today = 0
        if not df_history.empty:
            due_today = len(df_history[df_history["next_review"] <= now])

        total_reviewed = len(df_history)
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("📅 Due Today", due_today)
        col_b.metric("✅ Total Reviewed", total_reviewed)
        col_c.metric("📚 Total Articles", len(df))

    st.markdown("---")

    # Mode: Due for Review
    if st.session_state.review_mode == "due":
        # Find articles due for review
        now = datetime.now()

        if df_history.empty:
            # No review history yet - suggest starting fresh
            st.info("👋 Welcome! You haven't reviewed any articles yet. Let's start with some random articles from your archive.")

            # Sample up to 10 random articles that actually have a summary.
            # The sample size has to come from the summarised population, not
            # from len(df): Matter-era articles arrive before enrichment runs,
            # so the pool of reviewable cards is routinely smaller than the
            # archive, and asking for more cards than exist raises.
            summarised = df[df["summary"].notna()]
            candidates = summarised.sample(min(10, len(summarised))) if not summarised.empty else summarised

            if candidates.empty:
                st.warning(
                    "No articles with AI summaries in the current selection, so there is "
                    "nothing to review yet. Run the enrichment pass "
                    "(`scripts/core/enrich_archive_gemini.py`), or widen the era and date "
                    "filters in the sidebar."
                )
            elif st.button(f"Start First Review Session ({len(candidates)} cards)", type="primary"):
                st.session_state.review_deck = candidates.to_dict('records')
                st.session_state.review_index = 0
                st.session_state.session_complete = False
                st.rerun()
        else:
            # Get articles due for review
            due_ids = df_history[df_history["next_review"] <= now]["article_id"].tolist()

            if not due_ids:
                st.success("🎉 You're all caught up! No articles due for review today.")
                st.info("Come back tomorrow, or try 'Browse by Topic' to review specific subjects.")

                # Show next review dates
                if not df_history.empty:
                    st.subheader("Upcoming Reviews")
                    upcoming = df_history.sort_values("next_review").head(10)
                    for _, row in upcoming.iterrows():
                        article = df[review_id_series(df) == row["article_id"]]
                        if not article.empty:
                            title = article.iloc[0]["title"]
                            days_until = (row["next_review"] - now).days
                            st.write(f"- **{title}** - in {days_until} days")
            else:
                st.info(f"📚 {len(due_ids)} articles are due for review today!")

                # Limit to 10 cards per session
                due_ids_session = due_ids[:10]
                candidates = df[review_id_series(df).isin(due_ids_session)]

                if st.button(f"Start Review Session ({len(candidates)} cards)", type="primary"):
                    st.session_state.review_deck = candidates.to_dict('records')
                    st.session_state.review_index = 0
                    st.session_state.session_complete = False
                    st.rerun()

    # Mode: Browse by Topic
    else:
        st.subheader("Browse by Topic")

        # Get all topics
        all_topics = [topic for topics in df["topics"] if topics is not None for topic in topics]
        if not all_topics:
            st.warning("No topics found. Please run enrichment first.")
            return

        topic_counts = pd.Series(all_topics).value_counts()
        selected_topic = st.selectbox("Choose a topic to review", topic_counts.index.tolist())

        if selected_topic:
            # Filter articles by topic
            topic_articles = df[df["topics"].apply(
                lambda x: x is not None and selected_topic in x
            )]

            st.info(f"Found {len(topic_articles)} articles about **{selected_topic}**")

            # Sample up to 10 articles
            candidates = topic_articles.sample(min(10, len(topic_articles)))

            if st.button(f"Review {selected_topic} ({len(candidates)} cards)", type="primary"):
                st.session_state.review_deck = candidates.to_dict('records')
                st.session_state.review_index = 0
                st.session_state.session_complete = False
                st.rerun()

    # Flashcard Interface
    if st.session_state.review_deck and not st.session_state.session_complete:
        st.markdown("---")

        deck = st.session_state.review_deck
        idx = st.session_state.review_index

        if idx >= len(deck):
            # Session complete
            st.session_state.session_complete = True
            st.rerun()

        article = deck[idx]

        # Progress bar
        progress = (idx) / len(deck)
        st.progress(progress, text=f"Card {idx + 1} of {len(deck)}")

        # Flashcard display
        st.markdown("### 📖 Article Review")

        # Title and Date
        title = article.get("title", "Untitled")
        date_saved = article.get("date_saved")
        if hasattr(date_saved, "strftime"):
            date_str = date_saved.strftime("%B %d, %Y")
        else:
            date_str = str(date_saved)

        st.markdown(f"## {title}")
        st.caption(f"Saved on {date_str}")

        # TL;DR Summary
        summary = article.get("summary", "No summary available")
        st.info(f"**TL;DR:** {summary}")

        # Metadata
        col1, col2, col3 = st.columns(3)
        with col1:
            if article.get("author"):
                st.markdown(f"**Author:** {article['author']}")
        with col2:
            if article.get("emotion"):
                st.markdown(f"**Tone:** {article['emotion']}")
        with col3:
            if article.get("word_count"):
                st.markdown(f"**Length:** {article['word_count']:,} words")

        # Topics and concepts
        tags = []
        topics = article.get("topics")
        if topics is not None and not (isinstance(topics, float) and pd.isna(topics)):
            if isinstance(topics, (list, np.ndarray)):
                tags.extend(list(topics))
        concepts = article.get("concepts")
        if concepts is not None and not (isinstance(concepts, float) and pd.isna(concepts)):
            if isinstance(concepts, (list, np.ndarray)):
                tags.extend(list(concepts))
        if tags:
            st.write("🏷️ " + ", ".join([f"`{t}`" for t in tags[:10]]))

        # Original article link
        if article.get("url"):
            st.markdown(f"🔗 [Read Original Article]({article['url']})")

        # Expandable full content
        if article.get("file_path"):
            with st.expander("📄 View Full Article Content"):
                try:
                    file_path = Path(article["file_path"])
                    if file_path.exists():
                        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                            post = frontmatter.load(f)
                            # Show content (truncated if very long)
                            content = post.content
                            if len(content) > 5000:
                                st.markdown(content[:5000] + "\n\n*[Content truncated for display]*")
                            else:
                                st.markdown(content)
                    else:
                        st.warning("File not found on this machine.")
                except Exception as e:
                    st.error(f"Error loading content: {e}")

        st.markdown("---")

        # Rating buttons
        st.markdown("### 🎯 How well do you remember this article?")

        col1, col2, col3, col4 = st.columns([1, 1, 1, 2])

        with col1:
            if st.button("😰 Hard", use_container_width=True, type="secondary"):
                # Record review
                df_history = load_review_history()
                article_id = review_id(article)
                df_history = update_review_record(article_id, 0, df_history)
                save_review_history(df_history)

                # Move to next card
                st.session_state.review_index += 1
                st.rerun()

        with col2:
            if st.button("🤔 Good", use_container_width=True, type="secondary"):
                # Record review
                df_history = load_review_history()
                article_id = review_id(article)
                df_history = update_review_record(article_id, 1, df_history)
                save_review_history(df_history)

                # Move to next card
                st.session_state.review_index += 1
                st.rerun()

        with col3:
            if st.button("✅ Easy", use_container_width=True, type="primary"):
                # Record review
                df_history = load_review_history()
                article_id = review_id(article)
                df_history = update_review_record(article_id, 2, df_history)
                save_review_history(df_history)

                # Move to next card
                st.session_state.review_index += 1
                st.rerun()

        with col4:
            if st.button("⏭️ Skip", use_container_width=True):
                # Skip without recording
                st.session_state.review_index += 1
                st.rerun()

        st.caption("💡 **Hard**: I need to review this again soon | **Good**: I remember the key points | **Easy**: I remember this well")

    # Session complete screen
    elif st.session_state.session_complete:
        st.success("🎉 Review session complete!")
        st.balloons()

        st.markdown("### Great work! You've completed this review session.")
        st.info("Come back later to review more articles and reinforce your knowledge.")

        if st.button("Start New Session", type="primary"):
            st.session_state.review_deck = []
            st.session_state.review_index = 0
            st.session_state.session_complete = False
            st.rerun()

if __name__ == "__main__":
    main()


