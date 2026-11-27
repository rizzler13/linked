FROM python:3.12-slim

RUN useradd -m -u 1000 appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# libatomic1 is required by Playwright's Node binary on Debian slim before --with-deps can run
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && rm -rf /var/lib/apt/lists/*

# Install Chromium + all its system deps for Playwright (fetch fallback + PDF rendering)
# PLAYWRIGHT_BROWSERS_PATH puts the binary in /app so appuser can reach it
ENV PLAYWRIGHT_BROWSERS_PATH=/app/.playwright
RUN python -m playwright install --with-deps chromium

COPY app/ ./app/
COPY static/ ./static/

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 4000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "4000"]
