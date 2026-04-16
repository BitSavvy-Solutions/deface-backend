FROM python:3.9
WORKDIR /app

RUN apt-get update && apt-get install -y ffmpeg

RUN pip install --timeout=200 --retries=5 flask
RUN pip install --timeout=200 --retries=5 opencv-python
RUN pip install --timeout=200 --retries=5 deface

COPY app.py app.py
COPY templates/ templates/

CMD ["python", "app.py"]