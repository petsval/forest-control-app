FROM python:3.12-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -r requirements.txt
ENV PORT=8010
ENV DATA_DIR=/data
EXPOSE 8010
CMD ["python", "app.py"]
