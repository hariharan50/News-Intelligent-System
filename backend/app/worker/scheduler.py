from taskiq import TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource

from app.worker.broker import broker

# Run exactly one scheduler container; more would enqueue every scheduled task twice.
scheduler = TaskiqScheduler(broker=broker, sources=[LabelScheduleSource(broker)])
