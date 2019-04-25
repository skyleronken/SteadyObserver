FROM python:3

COPY . /root
WORKDIR /root

RUN pip install --trusted-host pypi.python.org -r requirements.txt

EXPOSE 8020
WORKDIR /data
ENTRYPOINT [ "python3", "steadyobserver.py" ]