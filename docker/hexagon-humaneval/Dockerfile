FROM python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 runner
COPY verify.py /opt/evalhub/verify.py
COPY worker.py /opt/evalhub/worker.py
RUN chmod 0444 /opt/evalhub/verify.py /opt/evalhub/worker.py

USER 10001:10001
ENTRYPOINT ["python", "/opt/evalhub/verify.py"]
