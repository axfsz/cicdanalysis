FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN useradd --system --uid 10001 --create-home cicd && mkdir -p /data && chown cicd:cicd /data
COPY pyproject.toml /app/pyproject.toml
COPY --chown=cicd:cicd cicdanalysis /app/cicdanalysis
RUN pip install --no-cache-dir ".[telegram-user]"
USER 10001
EXPOSE 8080
ENTRYPOINT ["python3","-m","cicdanalysis"]
CMD ["serve"]
