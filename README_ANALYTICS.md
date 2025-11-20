# Instapaper Archive Analytics & Dashboard

Turn your static Instapaper markdown archive into an interactive, AI-powered personal knowledge base.

## Overview

This project transforms a folder of Markdown files (your Instapaper export) into a "Quantified Self" reading dashboard. It moves beyond simple storage to provide deep insights into *how* and *what* you read over the last decade.

### Key Features
1.  **Data Indexing**: Converts thousands of files into a fast, queryable Parquet dataset.
2.  **AI Enrichment**: Uses local LLMs (Ollama/Qwen) to tag articles with:
    *   **Topics**: High-level themes.
    *   **Entities**: Key People, Organizations, and Locations mentioned.
    *   **Concepts**: Important abstract ideas or products that characterize the article.
    *   **Emotion**: The emotional tone of the piece (e.g., "Optimistic", "Alarming").
    *   **Summary**: A multi-sentence TL;DR.
3.  **Interactive Dashboard**: A local Streamlit app with 4 main views:
    *   **The Quantified Reader**: High-level stats, timeline, reading rhythms, and complexity tracking.
    *   **Content Intelligence**: Visualizes topic landscapes and emotional arcs.
    *   **Network & Entities**: Shows which people and companies dominate your reading.
    *   **Archive Explorer**: Semantic search and filtering.

## Prerequisites

1.  **Python 3.11+**
2.  **Ollama** installed and running (`ollama serve`)
3.  **Model**: `qwen2.5:7b` pulled (`ollama pull qwen2.5:7b`)

## Setup

1.  **Install Dependencies**:
    ```bash
    # Create virtual environment
    python3 -m venv .venv
    source .venv/bin/activate

    # Install packages
    pip install -r requirements_dashboard.txt
    ```

2.  **Configure Paths**:
    *   Default Vault Path: `~/Obsidian/Vault/Instapaper`
    *   *Note: To change this, edit `VAULT_PATH` in `scripts/build_index.py`.*

## Usage Workflow

### 1. Build the Index (First Run)
Scan your markdown files and create the initial dataset.
```bash
python3 scripts/build_index.py
```
*Output: `data/archive_index.parquet`*

### 2. Analyze Articles (AI Enrichment)
Run the enrichment script to add deep metadata to your files. This modifies the Markdown files in place (adding YAML frontmatter).
```bash
# Process a batch of 50 articles (good for testing)
python3 scripts/enrich_archive.py 50

# Process ALL articles (may take several hours)
python3 scripts/enrich_archive.py
```
*Note: The script is idempotent. It skips files that already have the latest enrichment fields.*

### 3. Rebuild Index
After running enrichment, you **must** rebuild the index so the dashboard sees the new AI tags.
```bash
python3 scripts/build_index.py
```

### 4. Launch Dashboard
Start the local analytics app.
```bash
streamlit run dashboard/app.py
```
Open your browser to `http://localhost:8501`.

## Project Roadmap

### Completed
*   ✅ **Core Dashboard**: Basic stats, timeline, word counts.
*   ✅ **AI Pipeline**: Local LLM integration for Topics, Sentiment, Summary.
*   ✅ **Advanced Enrichment**: Named Entity Recognition (People/Orgs) and Emotional Tone.
*   ✅ **Reading Rhythms**: Day-of-week analysis and Complexity (Flesch-Kincaid) tracking.

### Future / Planned
*   **World Context Layer**: Overlay global historical events on the reading timeline to spot correlations (e.g., "Did I read about virology during the pandemic?").
*   **RAG Chat**: "Chat with your Archive" feature using vector embeddings to answer questions based on your reading history.
*   **Burst Detection**: Algorithmic detection of "Aha!" moments when new topics suddenly appeared in your feed.

## Project Structure

*   `scripts/build_index.py`: Scans markdown files, fixes encoding errors, extracts frontmatter, saves to Parquet.
*   `scripts/enrich_archive.py`: Connects to Ollama, generates deep insights, updates markdown frontmatter.
*   `dashboard/app.py`: The Streamlit visualization application.
*   `data/`: Stores the generated `archive_index.parquet`.
