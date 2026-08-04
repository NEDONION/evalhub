FROM python:3.11-slim

RUN python -m pip install --no-cache-dir "lm_eval[api,math,sentencepiece]==0.4.12"
