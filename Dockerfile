# HB Briefing - self-hosted container
#
# The web app is the entry point: one port, one volume, no desktop dependencies. The same image
# also carries the CLI and the terminal UI, so `docker compose run --rm briefing scan` works.
#
#   docker build -t hb-briefing .
#   docker run -d --name hb-briefing -p 8765:8765 -v hb-data:/data hb-briefing
#
# State (database, feeds, watchlist, topics, profiles, settings, .env) lives in /data and is
# created on first start from the bundled examples plus the active profile's starter feeds.
FROM python:3.12-slim

# tzdata: publication dates and "today" are local to the reader, not to the container.
# ca-certificates: every fetch is https. poppler-utils: PDF orders during deep reads.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates poppler-utils tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HB_BRIEFING_HOME=/data \
    TZ=UTC

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY hbversion.py hbnscan.py hbworkspace.py hbconfig.py hbnconfig_gui.py hbnscan_gui.py \
     hbntui.py hbnweb.py hbsimilar.py hbicons.py hbwasm.py ./
COPY profiles ./profiles
COPY docs ./docs
COPY feeds.txt.example watchlist.txt.example topics.txt.example .env.example LICENSE README.md ./
COPY packaging/docker/entrypoint.sh /usr/local/bin/hb-briefing

RUN chmod 0755 /usr/local/bin/hb-briefing \
 && mkdir -p /data \
 && useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin briefing \
 && chown -R briefing:briefing /data /app
USER briefing
VOLUME ["/data"]
EXPOSE 8765

# The web app answers /api/state without a token; that is enough to prove it is alive.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/state', timeout=4).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/hb-briefing"]
CMD ["web"]
