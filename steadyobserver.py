from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.mongodb import MongoDBJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from rocketchat_API.rocketchat import RocketChat
from slackclient import SlackClient
from aiohttp import web
from mongoengine import *
from models import Task, Result
import configparser
import logging
from pytz import utc
from threading import Lock
from datetime import datetime
import requests
import json
import ast
import jsondiff

scheduler_lock = None
scheduler = None


def get_latest_result_for_task(tid):
    latest = Result.objects(task=tid).order_by('-creation_date').first()
    print(latest.data)
    return latest


def send_request(r_type, task_id, method, url, body=None, headers=None):
    print("Send request")

    if not headers:
        headers = {'Content-Type': 'application/json'}

    if method == "GET":
        response = requests.get(url, headers=headers)
    elif method == "POST":
        response = requests.post(url, body, headers=headers)

    if r_type == "sync":
        data = response.json()

        # Get latest result
        latest_result = get_latest_result_for_task(task_id)

        # Create new Result
        new_result = Result(data=json.dumps(data))
        task = Task.objects.get(id=task_id)
        new_result.task = task
        new_result.save()

        # Update Task with Result
        task.results.append(new_result)
        task.save()

        diff_results(new_result.data, latest_result.data, task.notification_type, task.notification_args)

    elif r_type == "poll":
        pass

    return


# Results Handling
def diff_results(latest, previous, notification_type, notification_args):
    # return if no notification_type
    if not notification_type:
        return

    differences = jsondiff.diff(latest, previous, syntax='explicit', load=True)
    if differences:
        # insert
        # delete
        # update
        # discard // sets only
        # add // sets only
        send_notification(json.dumps(differences), notification_type, notification_args)
    else:
        return


# Notification actions
def send_slack_notification(token, channel, message):
    sc = SlackClient(token)
    sc.api_call(
        "chat.postMessage",
        channel=channel,
        text=message
    )


def send_rocketchat_notification(un, pw, url, channel, message):
    rocket = RocketChat(un, pw, server_url=url)
    rocket.chat_post_message(message, channel=channel, alias='Bot')


def send_notification(message, notification_type, notification_args):

    parsed_args = json.loads(notification_args)
    channel = parsed_args.get('channel', 'GENERAL')

    if notification_type == "slack":
        token = parsed_args.get('token')
        send_slack_notification(token, channel, message)
    elif notification_type == "rocketchat":
        un = parsed_args.get('un')
        pw = parsed_args.get('pw')
        url = parsed_args.get('url')
        send_rocketchat_notification(un, pw, url, channel, message)


# API Endpoints

async def get_time(request):
    return web.json_response({"time": "{}".format(datetime.now(utc).strftime("%Y-%m-%d %H:%M:%S"))})


async def get_tasks(request):
    logging.info("Get Tasks")

    tasks = Task.objects()

    filter = None
    range = None
    sort = None
    if 'filter' in request.rel_url.query:
        filter = ast.literal_eval(request.rel_url.query['filter'])

    if 'range' in request.rel_url.query:
        range = ast.literal_eval(request.rel_url.query['range'])

    if 'sort' in request.rel_url.query:
        sort = ast.literal_eval(request.rel_url.query['sort'])

    task_count = tasks.count()

    if filter:
        pass

    if sort:
        field = sort[0]
        asc_desc = sort[1]

        if asc_desc == "ASC":
            field = "+{}".format(field)
        else:
            field = "-{}".format(field)

        tasks = tasks.order_by(field)

    if range:
        first = int(range[0])
        last = int(range[1])

        offset = first
        number = last - first
        tasks = tasks.skip(offset).limit(number)

    tasks = tasks.exclude('scheduler_id')
    return web.json_response(tasks.to_json(), dumps=str)


async def get_task(request):
    logging.info("Get Task")

    tid = request.match_info['id']
    try:
        task = Task.objects.get(id=tid)
        job = scheduler.get_job(task.scheduler_id)
        task.next_run_time = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
    except DoesNotExist:
        return web.json_response({})

    return web.json_response(task.to_json(), dumps=str)


async def create_task(request):
    logging.info("Create Task")

    data = await request.json()

    task_type = data.get('task_type', None)
    task_args = data.get('task_args', [])
    time_interval = data.get('time_interval', 'week')
    time_multiplier = data.get('time_multiplier', 1)

    # define job function
    job_func = None

    # setup for future task types
    if task_type == "request":
        job_func = send_request

    # calculate time
    weeks = 0
    days = 0
    hours = 0
    minutes = 0

    if time_interval == "month":
        weeks = time_multiplier * 4
    elif time_interval == "week":
        weeks = time_multiplier
    elif time_interval == "day":
        days = time_multiplier
    elif time_interval == "hour":
        hours = time_multiplier
    elif time_interval == "minute":
        minutes = time_multiplier

    r_type = data.get('response_type', 'async')

    # create task
    new_task = Task(
        description=data.get('description', None),
        task_type=task_type,
        task_args=task_args,
        notification_type=data.get('notification_type', None),
        notification_args=data.get('notification_args', None),
        response_type=r_type,
        response_args=data.get('response_args', None),
        time_interval=time_interval,
        time_multiplier=time_multiplier,
        diff=data.get('diff', None),
        status='active'
    )
    new_task.save()

    comb_task_args = [new_task.id] + task_args
    comb_task_args = [r_type] + comb_task_args

    # schedule job
    scheduler_lock.acquire()
    job = scheduler.add_job(func=job_func,
                            trigger='interval',
                            args=comb_task_args,
                            weeks=weeks,
                            days=days,
                            hours=hours,
                            minutes=minutes,
                            seconds=0,
                            coalesce=True)
    scheduler_lock.release()

    new_task.scheduler_id = job.id
    new_task.save()

    return web.json_response(new_task.to_json(), dumps=str)


async def delete_task(request):
    logging.info("Delete Task")

    tid = request.match_info['id']
    try:
        task = Task.objects.get(id=tid)

        scheduler_lock.acquire()
        job = scheduler.get_job(task.scheduler_id)
        job.remove()
        scheduler_lock.release()

        results = Result.objects(task=task.id)
        results.delete()
        task.delete()
    except DoesNotExist:
        return web.json_response({"success": False, "message": "Does not exist"})

    return web.json_response({"success": True})


async def suspend_task(request):
    logging.info("Suspend Task")

    tid = request.match_info['id']
    try:
        task = Task.objects.get(id=tid)
        job = scheduler.get_job(task.scheduler_id)
        job.pause()
        task.status = "suspended"
        task.save()
    except DoesNotExist:
        return web.json_response({"success": False, "message": "Does not exist"})

    return web.json_response({"success": True})


async def resume_task(request):
    logging.info("Resume Task")

    tid = request.match_info['id']
    try:
        task = Task.objects.get(id=tid)
        job = scheduler.get_job(task.scheduler_id)
        job.resume()
        task.status = "active"
        task.save()
    except DoesNotExist:
        return web.json_response({"success": False, "message": "Does not exist"})

    return web.json_response({"success": True})


async def get_results(request):
    logging.info("Get Results for Task")

    tid = request.match_info['id']
    results = Result.objects(task=tid)

    return web.json_response(results.to_json(), dumps=str)


async def get_result(request):
    logging.info("Get Result")

    rid = request.match_info['id']
    try:
        result = Result.objects.get(id=rid)
    except DoesNotExist:
        return web.json_response({})

    return web.json_response(result.to_json(), dumps=str)


async def post_result(request):
    logging.info("Create Result for Task")

    data = await request.json()
    tid = request.match_info['id']
    try:
        task = Task.objects.get(id=tid)
    except DoesNotExist:
        return web.json_response({})

    # Get latest result
    latest_result = get_latest_result_for_task(tid)

    result_data = data.get('data', "")
    new_result = Result(data=result_data)
    new_result.task = task
    new_result.save()

    # Update Task with Result
    task.results.append(new_result)
    task.save()

    diff_results(new_result.data, latest_result.data, task.notification_type, task.notification_args)

    return web.json_response(new_result.to_json(), dumps=str)


async def delete_result(request):
    logging.info("Delete Result")

    rid = request.match_info['id']
    try:
        result = Result.objects.get(id=rid)
        result.delete()
    except DoesNotExist:
        return web.json_response({"success": False, "message": "Does not exist"})

    return web.json_response({"success": True})


if __name__ == '__main__':

    print("Starting SteadyObserver")

    print("Parsing config file")
    config = configparser.ConfigParser()
    config.read_file(open('config.ini'))

    listen_port = int(config.get('steadyobserver', 'listen_port'))
    listen_host = config.get('steadyobserver', 'listen_host')

    mongo_port = int(config.get('mongo', 'listen_port'))
    mongo_host = config.get('mongo', 'listen_host')
    #mongo_pass = config.get('mongo', 'password')
    #mongo_user = config.get('mongo', 'username')

    print("Building API")
    app = web.Application()
    app.add_routes([web.get('/', get_time),
                    web.get('/time', get_time),
                    web.get('/tasks', get_tasks),
                    web.get('/tasks/{id}', get_task),
                    web.get('/tasks/{id}/results', get_results),
                    web.get('/results/{id}', get_result),
                    web.post('/tasks', create_task),
                    web.post('/tasks/{id}/suspend', suspend_task),
                    web.post('/tasks/{id}/resume', resume_task),
                    web.post('/tasks/{id}/result', post_result),
                    web.delete('/tasks/{id}', delete_task),
                    web.delete('/results/{id}', delete_result)])

    connect('steadyobserver', host=mongo_host, port=mongo_port)

    print("Building scheduler data stores")
    job_stores = {'default': MongoDBJobStore(host=mongo_host, port=mongo_port)} # password=mongo_pass, username=mongo_user)}
    executors = {'default': ThreadPoolExecutor(20)}
    job_defaults = {'coalesce': False, 'max_instances': 3}

    scheduler = AsyncIOScheduler(jobstores=job_stores, executors=executors, job_defaults=job_defaults, timezone=utc)
    print("Starting scheduler")
    scheduler.start()
    scheduler.print_jobs()
    scheduler_lock = Lock()

    print("SteadyObserver listening...")
    web.run_app(app, host=listen_host, port=listen_port)
