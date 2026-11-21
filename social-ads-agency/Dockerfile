FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agency ./agency
COPY db ./db
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "agency.gateway:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
