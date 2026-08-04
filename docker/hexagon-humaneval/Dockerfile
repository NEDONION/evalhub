FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 runner
COPY verify.py /opt/evalhub/verify.py
RUN chmod 0444 /opt/evalhub/verify.py

USER 10001:10001
ENTRYPOINT ["python", "/opt/evalhub/verify.py"]
