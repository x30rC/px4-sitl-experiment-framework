"""Gazebo Transport relay for live x500 magnetic field and static pressure."""

from __future__ import annotations

import argparse
import signal
import threading
import time

from spoof_runtime import ActiveIdleSchedule, parse_finite_float

SENSORS = {
    "mag": ("magnetometer_sensor/magnetometer", "PX4_GZ_MAG_TOPIC"),
    "baro": ("air_pressure_sensor/air_pressure", "PX4_GZ_BARO_TOPIC"),
}


def modify(message, sensor: str, offsets: tuple[float, ...], intensity: float):
    """Modify a copy, never the source message; magnetic values are Tesla, pressure Pa."""
    output = type(message)()
    output.CopyFrom(message)
    scale = intensity / 100.0
    if sensor == "mag":
        for axis, offset in zip(("x", "y", "z"), offsets):
            setattr(output.field_tesla, axis, getattr(output.field_tesla, axis) + offset * scale)
    else:
        output.pressure += offsets[0] * scale
        if output.pressure <= 0:
            raise ValueError("spoof pressure must remain positive")
    return output


class Relay:
    def __init__(self, args):
        self.args = args
        self.stop = threading.Event()
        self.active = threading.Event()
        self.started = None
        self.count = 0
        self.spoofed = 0
        self.publisher = None
        self.received = threading.Event()
        self.schedule = ActiveIdleSchedule(args.active_seconds, args.idle_seconds)
        self.time_mode = getattr(args, "time_mode", "sync")

    def activate(self, *_):
        self.active.set()

    def callback(self, message):
        if self.stop.is_set() or self.publisher is None:
            return
        # The simulator's stamp advances in simulation time, including when paused.
        stamp = message.header.stamp
        now = (stamp.sec + stamp.nsec / 1e9
               if self.time_mode == "sync" else time.monotonic())
        if self.active.is_set() and self.started is None:
            self.started = now
            print(f"{self.args.sensor}: атака активирована, длительность {self.args.duration:g} сек "
                  f"по времени {'Gazebo' if self.time_mode == 'sync' else 'монотонных часов'}", flush=True)
        elapsed = max(0.0, now - self.started) if self.started is not None else 0.0
        attacking = (self.started is not None and elapsed < self.args.duration
                     and self.schedule.is_active(elapsed))
        if attacking:
            offsets = (self.args.x, self.args.y, self.args.z) if self.args.sensor == "mag" else (self.args.pressure_offset,)
            output = modify(message, self.args.sensor, offsets, self.args.intensity)
            self.spoofed += 1
            if self.spoofed == 1:
                print(f"{self.args.sensor}: опубликовано первое подменённое измерение", flush=True)
        else:
            output = message
        self.publisher.publish(output)
        self.count += 1
        self.received.set()

    def run(self):
        from gz.transport13 import Node
        if self.args.sensor == "mag":
            from gz.msgs10.magnetometer_pb2 import Magnetometer as Message
        else:
            from gz.msgs10.fluid_pressure_pb2 import FluidPressure as Message
        node = Node()
        self.publisher = node.advertise(self.args.topic, Message)
        if self.publisher is None or not node.subscribe(Message, self.args.source_topic, self.callback):
            raise RuntimeError("Gazebo advertise/subscribe failed")
        print(f"{self.args.sensor} relay: {self.args.source_topic} -> {self.args.topic}; waiting for SIGUSR1", flush=True)
        try:
            if not self.received.wait(30) and not self.stop.is_set():
                raise RuntimeError(f"no source messages on {self.args.source_topic}")
            while not self.stop.wait(1):
                self.received.clear()
                if not self.received.wait(15) and not self.stop.is_set():
                    raise RuntimeError(f"source timed out: {self.args.source_topic}")
        finally:
            print(f"{self.args.sensor}: total={self.count}, spoofed={self.spoofed}", flush=True)
        return 0


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("sensor", choices=SENSORS)
    p.add_argument("--source-topic", required=True)
    p.add_argument("--topic", required=True)
    p.add_argument("--time-mode", choices=("sync", "async"), default="sync")
    for name, default in (("intensity", 100), ("duration", 60), ("active-seconds", 0),
                          ("idle-seconds", 0), ("x", 0), ("y", 0), ("z", 0),
                          ("pressure-offset", 0)):
        p.add_argument("--" + name, type=parse_finite_float, default=default)
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if not args.source_topic.startswith("/") or not args.topic.startswith("/") or args.topic == args.source_topic:
        p.error("source and output must be different absolute topics")
    if not 0 <= args.intensity <= 1000 or args.duration <= 0 or min(args.active_seconds, args.idle_seconds) < 0:
        p.error("invalid intensity, duration or phase")
    if (args.active_seconds == 0) != (args.idle_seconds == 0):
        p.error("both active/idle must be positive or both zero")
    relay = Relay(args)
    signal.signal(signal.SIGTERM, lambda *_: relay.stop.set())
    signal.signal(signal.SIGINT, lambda *_: relay.stop.set())
    signal.signal(signal.SIGUSR1, relay.activate)
    return relay.run()


if __name__ == "__main__":
    raise SystemExit(main())
