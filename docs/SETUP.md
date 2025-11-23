# Setup Guide

Complete installation and configuration guide for the Article Archive system.

## Prerequisites

- Python 3.8 or higher
- pip (Python package manager)
- Git

## Installation

### 1. Clone the Repository

```bash
git clone <your-repo-url>
cd Project\ -\ Instapaper\ Archive
```

### 2. Create Virtual Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate  # On macOS/Linux
# OR
venv\Scripts\activate     # On Windows
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

This installs all dependencies for:
- Instapaper export
- Legacy file import
- AI enrichment (both local and cloud)
- Analytics dashboard

### 4. Install Optional Dependencies

#### For Legacy .doc File Conversion:
```bash
# macOS
brew install --cask libreoffice

# Linux
sudo apt-get install libreoffice

# Windows
# Download from: https://www.libreoffice.org/
```

#### For Local LLM (Ollama):
```bash
# macOS
brew install ollama

# Then pull the model
ollama pull qwen2.5:14b-instruct
```

## Configuration

### 1. Copy Environment Template

```bash
cp env.example .env
```

### 2. Edit .env File

Open `.env` in your text editor and configure:

#### Instapaper API (Required for Instapaper export)
```bash
INSTAPAPER_CONSUMER_KEY=your_key
INSTAPAPER_CONSUMER_SECRET=your_secret
INSTAPAPER_USERNAME=your_email@example.com
INSTAPAPER_PASSWORD=your_password
INSTAPAPER_FOLDER=archive
```

Get API credentials from: https://www.instapaper.com/main/request_oauth_consumer_token

#### Vault Path (Where markdown files are stored)
```bash
INSTAPAPER_VAULT_PATH=/path/to/your/markdown/vault
```

#### Legacy Import Paths (Optional)
```bash
IMPORT_SOURCE_PATH=/path/to/legacy/files
IMPORT_OUTPUT_PATH=/path/to/markdown/output
```

#### Gemini API (Optional, for fast enrichment)
```bash
GEMINI_API_KEY=your_gemini_key
```

Get API key from: https://aistudio.google.com/apikey

## Verification

### Test Your Setup

```bash
# Verify Python version
python --version

# Verify pip packages installed
pip list | grep -E "streamlit|pandas|markitdown"

# Test Ollama (if using local enrichment)
ollama list

# Test configuration loaded
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print('Config loaded!' if os.getenv('INSTAPAPER_VAULT_PATH') else 'No vault path')"
```

## Next Steps

Once setup is complete:

1. **Export from Instapaper**: See main README
2. **Import legacy files**: See [docs/LEGACY_IMPORT.md](LEGACY_IMPORT.md)
3. **Enrich with AI**: See [docs/ENRICHMENT.md](ENRICHMENT.md)
4. **Launch dashboard**: `streamlit run dashboard/app.py`

## Troubleshooting

### "Module not found"
```bash
# Make sure venv is activated
source venv/bin/activate

# Reinstall requirements
pip install -r requirements.txt
```

### "Permission denied" on macOS
```bash
# Some tools may need full disk access
# System Preferences > Security & Privacy > Full Disk Access
```

### Port 8501 already in use
```bash
# Kill existing Streamlit
lsof -ti:8501 | xargs kill -9

# Or use different port
streamlit run dashboard/app.py --server.port 8502
```

## Performance Tips

### For Large Archives (10k+ articles):

1. **Use USB SSD** instead of network drives for processing
2. **Use Gemini API** for enrichment (30 mins vs 2 days)
3. **Process locally** then sync to NAS if needed

### Storage Optimization:

- Index files are machine-specific (don't sync between computers)
- Manifest files track progress per-machine
- Markdown files are the source of truth (sync these)

