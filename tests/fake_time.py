"""Test helper: a clock/sleep pair that advances only when slept on, so waiting costs no real time."""


class FakeTime:
    def __init__(self, on_sleep=None):
        self.now = 0.0
        self.sleeps = 0
        self.on_sleep = on_sleep  # called with the number of sleeps so far, to make things happen "while waiting"

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps += 1
        self.now += seconds
        if self.on_sleep:
            self.on_sleep(self.sleeps)
