FROM nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Flask and Deface
RUN pip3 install --no-cache-dir flask deface

# Remove the CPU version of ONNX Runtime and install the GPU version
RUN pip3 uninstall -y onnxruntime
RUN pip3 install --no-cache-dir onnxruntime-gpu

COPY . .

EXPOSE 5000

CMD ["python3", "app.py"]