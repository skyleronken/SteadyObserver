FROM python:3.7.3

COPY . /app
WORKDIR /app

RUN pip3 install --trusted-host pypi.python.org -r requirements.txt

EXPOSE 8080
ENTRYPOINT [ "python3", "steadyobserver.py" ]