# Imagen base ligera de Python
FROM python:3.12-slim

WORKDIR /app

# Instala dependencias primero (aprovecha la cache de Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia el codigo del servidor
COPY server.py .

# Carpeta donde vivirá la base de datos SQLite
RUN mkdir -p /app/data
ENV DB_PATH=/app/data/llamadas.db

EXPOSE 5000

# Usamos 1 worker para proteger la memoria temporal de agrupación de llamadas (_llamadas_pendientes)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", "server:app"]