# AI Enrichment Guide

Extract structured insights from your articles using AI: topics, people, organizations, locations, concepts, sentiment, and summaries.

## Overview

The enrichment scripts analyze each article's content and add structured metadata to the frontmatter. This enables powerful search, filtering, and visualization in the dashboard.

## Two Options

### Option 1: Gemini API (Recommended)

**Pros:**
- ⚡ Very fast (20-60 minutes for 10k articles)
- 💰 Very cheap (~$0.50 for 10k articles)
- 🎯 High quality entity extraction
- 🔄 Parallel processing (20 threads)

**Cons:**
- Requires API key
- Sends article text to Google (privacy consideration)
- Requires internet connection

### Option 2: Local Ollama

**Pros:**
- 🔒 Completely private (runs on your machine)
- 💵 Free (no API costs)
- ⚙️ Full control over model

**Cons:**
- ⏱️ Slower (20-40 hours for 10k articles)
- 💻 Requires beefy hardware (8GB+ RAM)
- 📦 Large model downloads (4-9GB)

## Using Gemini API (Fast)

### Setup

1. Get API key from: https://aistudio.google.com/apikey
2. Add to `.env`:
   ```bash
   GEMINI_API_KEY=your_key_here
   ```

### Usage

```bash
# Enrich all un-enriched articles
python scripts/core/enrich_archive_gemini.py

# Force re-process everything (if you want to update with better model/prompt)
python scripts/core/enrich_archive_gemini.py force

# Test on small batch first
python scripts/core/enrich_archive_gemini.py 10
```

**Cost estimate displayed before processing!**

### Performance
- Model: `gemini-2.5-flash-lite`
- Speed: ~300-900 articles/minute
- Content analyzed: First 10,000 characters per article
- Cost: ~$0.04 per 1,000 articles

## Using Local Ollama (Free)

### Setup

1. Install Ollama: https://ollama.ai/
2. Pull model:
   ```bash
   ollama pull qwen2.5:14b-instruct
   ```

### Usage

```bash
# Enrich articles (default: 10 at a time)
python scripts/core/enrich_archive.py 100

# Process everything
python scripts/core/enrich_archive.py 99999
```

### Performance
- Model: `qwen2.5:14b-instruct` (9GB)
- Speed: ~10-15 seconds per article
- First call: 1-5 minutes to load model into memory
- Subsequent calls: Fast

## What Gets Extracted

For each article, the AI adds:

```yaml
ai_topics: [Technology, Innovation, Business]
ai_people: [Steve Jobs, Tim Cook]
ai_orgs: [Apple, Google, Microsoft]
ai_locations: [California, USA, Cupertino]
ai_concepts: [Product Design, User Experience, Market Strategy]
ai_sentiment: Positive
ai_emotion: Inspiring
ai_summary: "A 2-3 sentence summary of the article..."
```

## Content Validation

Both scripts include automatic corruption detection:
- Identifies sidebar/navigation content
- Flags articles where content doesn't match title
- Skips already-corrupted articles
- See [docs/CORRUPTION_HANDLING.md](CORRUPTION_HANDLING.md) for details

## After Enrichment

### Rebuild Index
```bash
python scripts/core/build_index.py
```

The parquet index makes dashboard queries fast.

### Launch Dashboard
```bash
streamlit run dashboard/app.py
```

Explore your enriched archive!

## Cost Comparison (10,000 articles)

| Method | Time | Cost | Quality | Privacy |
|--------|------|------|---------|---------|
| **Gemini Flash-Lite** | 30-60 min | $0.40 | Excellent | ❌ Cloud |
| **Gemini Flash** | 30-60 min | $2.00 | Excellent | ❌ Cloud |
| **Local qwen2.5:14b** | 40-50 hrs | $0 | Good | ✅ Private |
| **Local qwen2.5:7b** | 20-25 hrs | $0 | Fair | ✅ Private |

## Adjusting Content Length

By default, 10,000 characters are sent per article. To adjust:

**In `scripts/core/enrich_archive_gemini.py`:**
```python
{content[:10000]}  # Change this number (line ~68)
```

**Trade-offs:**
- More chars = better context, higher cost, slower
- Less chars = cheaper, faster, may miss entities mentioned late

**Recommendations:**
- 3,500 chars: Bare minimum (~$0.20 for 10k)
- 7,000 chars: Good balance (~$0.40 for 10k)
- 10,000 chars: Recommended (~$0.60 for 10k)
- Full article: Highest quality (~$1-3 for 10k)

## Troubleshooting

### "Ollama connection failed"
```bash
# Check if Ollama is running
ollama list

# Start Ollama server
ollama serve
```

### "Gemini API error"
- Check API key in `.env`
- Verify billing is enabled in Google AI Studio
- Check rate limits (free tier: 15 RPM, paid: 2000 RPM)

### "Index not found"
```bash
# Build index first
python scripts/core/build_index.py
```

### Slow Performance
- Use USB SSD instead of network drives
- Use Gemini API instead of local model
- Reduce `MAX_WORKERS` if hitting rate limits

