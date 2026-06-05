FROM python:3.11-slim

RUN useradd -m -u 1000 user

# Install system dependencies for matplotlib
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY --chown=user app.py .
COPY --chown=user prompts/ prompts/
COPY --chown=user screenshots/ screenshots/
COPY --chown=user .streamlit .streamlit

USER user

ENV HOME=/home/user
ENV PATH=/home/user/.local/bin:$PATH
ENV MPLCONFIGDIR=/tmp/matplotlib
ENV STREAMLIT_HOME=/home/user/app/.streamlit

EXPOSE 7860

CMD ["streamlit", "run", "app.py", "--server.port=7860", "--server.address=0.0.0.0", "--server.headless=true"]
