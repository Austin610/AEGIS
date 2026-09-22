FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres]" && useradd --create-home --uid 10001 aegis && mkdir /data && chown aegis:aegis /data
USER aegis
EXPOSE 8766
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8766/health', timeout=2)"
ENTRYPOINT ["aegis"]
CMD ["serve", "--data-dir", "/data", "--container"]
