# link-ed

AI-powered website intelligence API. Drop in a URL, get competitor analysis, SEO recommendations, search queries, services, and audience profiling — in seconds.

> Built as a submission for the Dxora Technologies Python test.

---

## Live

| | |
|---|---|
| **Dashboard** | https://linked-6oi2.onrender.com |
| **Docs** | https://linked-6oi2.onrender.com/docs |

> Free tier on Render — first request after 15 min idle may take ~30s. Ping `/health` before demoing.

## [Why is this special?](cooked.md)

---
<img width="1041" height="820" alt="image" src="https://github.com/user-attachments/assets/3788af2b-ed21-459d-85c3-f57f7385fe22" />


## Quick Start

```bash
git clone https://github.com/rizzler13/linked && cd linked
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # fill in GEMINI_API_KEY and GROQ_API_KEY
uvicorn app.main:app --host 0.0.0.0 --port 4000
```

---
### Competitor Analysis
<img width="941" height="820" alt="image" src="https://github.com/user-attachments/assets/07b81c70-14d8-441b-bab2-9bef0e47eb2b" />

### Search Queries
<img width="904" height="586" alt="image" src="https://github.com/user-attachments/assets/47d5558e-e315-4083-8588-7587b25024c9" />

### SEO
<img width="879" height="652" alt="image" src="https://github.com/user-attachments/assets/53f6e8cd-96ab-419f-ae4f-0b1ae69f3b10" />


### Services
<img width="943" height="814" alt="image" src="https://github.com/user-attachments/assets/7904397b-cb1c-4041-a256-06e4391fafdf" />


### Audience
<img width="934" height="776" alt="image" src="https://github.com/user-attachments/assets/3d6b768c-7828-4942-a22a-358e053b4612" />



## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/link-analysis` | Full analysis as JSON |
| `POST` | `/link-analysis/stream` | Same analysis via SSE — streams partial results live |
| `POST` | `/link-analysis/pdf` | Download analysis as PDF |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | API documentation |

All analysis endpoints accept `{ "url": "https://example.com" }`.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | Yes | Google Gemini API key |
| `GROQ_API_KEY` | Yes | Groq API key |
| `OPENROUTER_API_KEY` | No | Optional Gemini fallback via OpenRouter |
| `APP_ENV` | No | `development` / `production` (default: `development`) |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` (default: `INFO`) |

---

## Tests

```bash
pytest                 # run all 84 tests
pytest --cov=app tests/
```

No API keys needed — LLM providers are mocked.
