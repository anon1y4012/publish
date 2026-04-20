FROM python:3.12-slim

LABEL org.opencontainers.image.title="EpubSync"
LABEL org.opencontainers.image.description="Personal EPUB inbox server — drop books in, Kindle pulls them on wake"
LABEL org.opencontainers.image.source="https://github.com/YOUR_USERNAME/epubsync"
LABEL org.opencontainers.image.licenses="MIT"

# Create non-root user
RUN addgroup --system epubsync && adduser --system --ingroup epubsync epubsync

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY server.py .

# Inbox volume mount point
RUN mkdir -p /inbox && chown epubsync:epubsync /inbox

USER epubsync

ENV EPUBSYNC_INBOX=/inbox \
    EPUBSYNC_PORT=8765 \
    EPUBSYNC_TOKEN="" \
    EPUBSYNC_HOST=0.0.0.0

EXPOSE 8765

VOLUME ["/inbox"]

CMD ["python", "server.py"]
