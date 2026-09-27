FROM python:3.12-slim

WORKDIR /app

# 파이썬 버퍼링 해제 (로그 실시간 출력)
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
