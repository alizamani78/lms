FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd -m -u 10001 app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN DJANGO_SECRET_KEY=build DJANGO_ALLOWED_HOSTS=build python manage.py collectstatic --noinput
USER app
CMD ["gunicorn", "config.wsgi:application", "-c", "gunicorn.conf.py"]
