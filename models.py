from mongoengine import *
import mongoengine_goodjson as gj
import datetime


class Result(gj.Document):
    creation_date = DateTimeField(default=datetime.datetime.utcnow)
    data = StringField(required=True)
    task = ReferenceField('Task')


class Task(gj.Document):
    description = StringField(required=False, max_length=200)
    scheduler_id = StringField(required=False)
    task_type = StringField(required=True, default='request', choices=['request'])
    task_args = ListField(StringField(), required=True, default=list)
    notification_type = StringField(required=False, choices=['slack', 'rocketchat'])
    notification_args = StringField(required=False, max_length=200)
    response_type = StringField(required=False, default='async', choices=['async', 'sync', 'poll'])
    response_args = StringField(required=False, max_length=200)
    time_interval = StringField(required=True, choices=['month', 'week', 'day', 'hour', 'minute'])
    time_multiplier = IntField(required=False, default=1)
    diff = BooleanField(required=True, default=False)
    next_run_time = StringField(required=False)
    status = StringField(required=False, default='active', choices=['active', 'suspended', 'running'])
    results = ListField(ReferenceField(Result))
