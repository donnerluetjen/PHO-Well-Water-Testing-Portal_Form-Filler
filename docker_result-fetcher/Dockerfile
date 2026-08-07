# Runs water_result_fetcher.py on demand, on any Docker host. See
# README.md for the full deployment flow.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    # --with-deps installs exactly the system libraries needed for
    # whichever Chromium version `pip install playwright` just pulled in,
    # so we don't have to pin/guess a matching base image ourselves.
    && playwright install --with-deps chromium

COPY water_result_logic.py water_result_fetcher.py ./

# There's no long-running service -- no webhook, no listening port.
# Whatever scheduler you use (DSM's Task Scheduler, cron, etc.) runs
# this image periodically via:
#   docker compose run --rm water-tester python water_result_fetcher.py
# (see README.md). CMD below is just the default if the container is
# started without an explicit command.
CMD ["python", "water_result_fetcher.py"]
