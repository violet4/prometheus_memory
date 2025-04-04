import time
import re
import subprocess
from prometheus_client import REGISTRY, start_http_server, Gauge
from prometheus_client.metrics_core import GaugeMetricFamily
from prometheus_client.registry import Collector
from sqlalchemy import create_engine, Column, Integer, Float, String
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()


class PingMetric(Base):
    __tablename__ = 'ping_metrics'

    id = Column(Integer, primary_key=True)
    timestamp = Column(Float)
    target = Column(String)
    latency = Column(Float)


class ScrapeTimeMetric(Base):
    __tablename__ = 'scrape_time_metrics'

    id = Column(Integer, primary_key=True)
    timestamp = Column(Float)

DATABASE_URI = 'sqlite:///metrics.db'
engine = create_engine(DATABASE_URI)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

PING_TARGETS = ['1.1.1.1']  # Add more targets if needed
PING_COUNT = 1
PING_TIMEOUT = 1

ping_latency = Gauge('ping_latency_seconds', 'Latency of ping in seconds', ['target'])

def ping(target):
    try:
        output = subprocess.check_output(
            ['ping', '-c', str(PING_COUNT), '-W', str(PING_TIMEOUT), target],
            stderr=subprocess.STDOUT
        )
        lines = output.decode().split('\n')
        for line in lines:
            if 'rtt min/avg/max/mdev' in line:
                return float(line.split('/')[4])
    except subprocess.CalledProcessError:
        pass

    return None

def store_ping_metric(session, target, latency):
    metric = PingMetric(timestamp=time.time(), target=target, latency=latency)
    session.add(metric)
    session.commit()


class ScrapeDelaySecondsCollector(Collector):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._collected_first = False

    def collect(self):
        metric = GaugeMetricFamily('prometheus_seconds_since_last_scrape', 'Seconds since last scrape')
        # collect is called as soon as we register our collector, which would
        # interfere with our collection timestamps. here we ignore the first
        # call.
        if not self._collected_first:
            self._collected_first = True
            yield metric
            return

        scrape_time = time.time()
        session = Session()

        record = session.query(ScrapeTimeMetric).order_by(ScrapeTimeMetric.id.desc()).limit(1).one_or_none()
        last_scrape_time = record.timestamp if record else time.time()
        seconds_since_last_scrape = scrape_time - last_scrape_time

        metric.add_metric([], seconds_since_last_scrape)

        new_record = ScrapeTimeMetric(timestamp=scrape_time)
        session.add(new_record)
        session.commit()
        yield metric

REGISTRY.register(ScrapeDelaySecondsCollector())

def get_default_route():
    ip_r_output = subprocess.check_output(['ip', 'r']).decode()
    default_route_ip = re.search(r'default via ((\d+\.){3}\d+)', ip_r_output)
    if not default_route_ip:
        return None
    return default_route_ip.group(1)


if __name__ == '__main__':
    start_http_server(9435)
    session = Session()

    while True:
        # we recalculate the default route each time
        # in case it changes
        default_route = get_default_route()
        additional_targets = [default_route] if default_route is not None else []
        for target in PING_TARGETS + additional_targets:
            latency = ping(target)
            if latency is not None:
                ping_latency.labels(target=target).set(latency)
                store_ping_metric(session, target, latency)

        time.sleep(15)

