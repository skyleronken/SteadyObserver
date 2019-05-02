FROM python:3.7.3-alpine3.8

COPY . /app
WORKDIR /app

RUN pip install --trusted-host pypi.python.org -r requirements.txt

EXPOSE 8080
ENTRYPOINT [ "python3", "steadyobserver.py" ]