FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home app \
    && mkdir /data && chown app:app /data
WORKDIR /app
COPY controller/requirements.txt /app/controller/requirements.txt
RUN pip install --no-cache-dir -r controller/requirements.txt
COPY --chown=app:app controller/ /app/controller/
USER app
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "controller.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
