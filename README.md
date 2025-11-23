# Article Archive - Export, Enrich, and Explore

A comprehensive toolkit for building a searchable, AI-enriched archive from multiple article sources (Instapaper, PDFs, Word docs, HTML, TXT files) with an interactive analytics dashboard.

## 🎯 What This Does

1. **📥 Import** articles from multiple sources:
   - Instapaper API export
   - Legacy PDFs, Word documents, HTML, TXT files
2. **🤖 AI Enrichment** - Extract topics, people, organizations, concepts, sentiment
3. **📊 Analytics Dashboard** - Visualize 20+ years of reading with interactive charts
4. **🧠 Spaced Review** - Remember what you've read using spaced repetition

## 🚀 Quick Start

### 1. Install Dependencies

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install all dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
# Copy example configuration
cp env.example .env

# Edit .env with your credentials and paths
# See env.example for all available options
```

### 3. Export from Instapaper (Optional)

```bash
python scripts/core/export_instapaper_to_obsidian.py
```

### 4. Import Legacy Files (Optional)

```bash
# Import PDFs, Word docs, HTML, TXT files
python scripts/core/import_legacy_archive.py
```

### 5. Enrich with AI

```bash
# Fast (Gemini API, ~30 mins for 10k articles, ~$0.50)
python scripts/core/enrich_archive_gemini.py

# Or Free (Local Ollama, slower but private)
python scripts/core/enrich_archive.py
```

### 6. Build Index & Launch Dashboard

```bash
# Build searchable index
python scripts/core/build_index.py

# Launch analytics dashboard
streamlit run dashboard/app.py
```

## 📁 Project Structure

```
├── docs/                      # Documentation
│   ├── SETUP.md              # Detailed setup guide
│   ├── LEGACY_IMPORT.md      # PDF/Word/TXT import guide
│   ├── ENRICHMENT.md         # AI enrichment options
│   ├── CORRUPTION_HANDLING.md# Data quality management
│   └── DASHBOARD.md          # Dashboard features
│
├── scripts/
│   ├── core/                 # Main workflow scripts
│   │   ├── export_instapaper_to_obsidian.py
│   │   ├── import_legacy_archive.py
│   │   ├── enrich_archive.py
│   │   ├── enrich_archive_gemini.py
│   │   └── build_index.py
│   │
│   ├── cleanup/              # Data quality tools
│   ├── analysis/             # Investigation utilities
│   └── diagnostic/           # API testing tools
│
├── dashboard/                # Analytics dashboard
│   └── app.py
│
└── data/                     # Generated data (gitignored)
```

## ✨ Features

### 📊 Analytics Dashboard
- **The Quantified Reader** - Reading statistics, achievements, comparisons
- **Content Intelligence** - AI insights, word clouds, concept evolution
- **Network & Entities** - People, organizations, locations mentioned
- **Trends Over Time** - Track concepts, topics, locations across 20 years
- **Heatmap Analysis** - Geographic focus, topic popularity, sentiment shifts
- **Spaced Review** - Flashcard-style review system for retention
- **Archive Explorer** - Search and filter your entire collection

### 🤖 AI Enrichment
Automatically extracts from each article:
- Topics & concepts
- People & organizations mentioned
- Locations
- Sentiment & emotional tone
- TL;DR summaries
- Content validation (detects corrupted/sidebar content)

### 📥 Multi-Source Import
- **Instapaper API** - Direct export via OAuth
- **PDFs** - Converts to markdown with LibreOffice fallback
- **Word Documents** - Handles both .doc and .docx
- **HTML/TXT/RTF** - Direct conversion
- **Unified format** - Everything becomes markdown with YAML frontmatter

## 📖 Documentation

- **[Setup Guide](docs/SETUP.md)** - Installation and configuration
- **[Legacy Import](docs/LEGACY_IMPORT.md)** - Import PDFs and other formats
- **[Enrichment Guide](docs/ENRICHMENT.md)** - AI processing options
- **[Data Quality](docs/CORRUPTION_HANDLING.md)** - Handling corrupted content
- **[Dashboard Features](docs/DASHBOARD.md)** - Using the analytics dashboard
- **[API Limitations](docs/API_LIMITATIONS.md)** - Known Instapaper API issues

## 🔧 Requirements

- Python 3.8+
- For local enrichment: Ollama with qwen2.5:14b model
- For fast enrichment: Google Gemini API key (get from [AI Studio](https://aistudio.google.com/apikey))
- For PDF conversion: LibreOffice (for legacy .doc files)

## 🎓 Use Cases

- **Personal Knowledge Management** - Unified archive of 20 years of reading
- **Research** - Track trends, people, organizations over time
- **Learning** - Spaced repetition review system
- **Analysis** - Visualize reading patterns and content insights
- **Migration** - Move from Instapaper/web clipping to markdown

## 📝 License

See LICENSE file for details.

## 🙏 Acknowledgments

Built with:
- [Streamlit](https://streamlit.io/) - Dashboard framework
- [Gemini API](https://ai.google.dev/gemini-api) - AI enrichment
- [MarkItDown](https://github.com/microsoft/markitdown) - Document conversion
- [Ollama](https://ollama.ai/) - Local LLM option
