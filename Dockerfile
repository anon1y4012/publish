FROM python:3.12-slim

LABEL org.opencontainers.image.title="Publish"
LABEL org.opencontainers.image.description="Personal book delivery server — drop files in, Kindle pulls on wake"
LABEL org.opencontainers.image.source="https://github.com/anon1y4012/publish"
LABEL org.opencontainers.image.licenses="MIT"

RUN addgroup --system publish && adduser --system --ingroup publish publish

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

RUN mkdir -p /inbox && chown publish:publish /inbox

USER publish

ENV PUBLISH_INBOX=/inbox \
    PUBLISH_PORT=8765 \
    PUBLISH_TOKEN="" \
    PUBLISH_HOST=0.0.0.0

EXPOSE 8765
VOLUME ["/inbox"]

CMD ["python", "server.py"]
