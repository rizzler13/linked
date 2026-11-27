FROM python:3.12-slim

RUN useradd -m -u 1000 appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
