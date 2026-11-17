"""Scheduler, send-time formula and output adapters (task 010)."""

from __future__ import annotations

import os
import random
import statistics
import time

import pytest
from fakes import make_group_decision, make_scheduled_output

from hwsorter.clock import NS_PER_MS, ManualClock, MonotonicClock
from hwsorter.config import TimingConfig
from hwsorter.contracts import MissReason, Output, OutputAdapter, normalise_for_compare
from hwsorter.eventlog import EventLog
from hwsorter.output import MockSerialAdapter, ScreenLogAdapter
from hwsorter.schedule import Scheduler, send_time_ns


def _wait(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.002)
    return pred()


def test_send_time_formula_numbers():
    timing = TimingConfig(exit_to_belt_end_in=12.0, collector_lead_ms=500.0)
    d = make_group_decision(t_ref_ns=1_000_000_000, x_lead_in_at_ref=2.0)
    # d_remaining = (10 - 2) + 12 = 20 in; at 4 in/s = 5 s; minus 0.5 s lead
    assert send_time_ns(d, 10.0, timing, 4.0) == 1_000_000_000 + 5_000_000_000 - 500_000_000
    # lead edge already past the exit line: d_remaining = (10 - 12) + 12 = 10 in -> 2.5 s
    d2 = make_group_decision(t_ref_ns=0, x_lead_in_at_ref=12.0)
    assert send_time_ns(d2, 10.0, timing, 4.0) == 2_500_000_000 - 500_000_000
    with pytest.raises(ValueError):
        send_time_ns(d, 10.0, timing, 0.0)


def test_replay_fires_in_t_send_group_order():
    clock, adapter, log = ManualClock(0), MockSerialAdapter(), EventLog()
    s = Scheduler(clock, adapter, log, replay=True)
    s.submit(make_scheduled_output(group_id=3, t_send_ns=200))
    s.submit(make_scheduled_output(group_id=2, t_send_ns=100))
    s.submit(make_scheduled_output(group_id=1, t_send_ns=100))
    assert [o.group_id for o in s.pending()] == [1, 2, 3]
    assert s.advance_to(99) == 0
    clock.advance_to(150)
    assert s.advance_to(150) == 2
    assert [o.group_id for o, _ in adapter.sent] == [1, 2]
    clock.advance_to(500)
    s.advance_to(500)
    assert [o.group_id for o, _ in adapter.sent] == [1, 2, 3]
    # a non-late send reports its target as the actual time in replay
    assert [t for _, t in adapter.sent] == [100, 100, 200]
    assert s.pending() == []


def test_single_send_record_written_by_scheduler():
    clock, adapter, log = ManualClock(0), ScreenLogAdapter(keep=5, uncalibrated=True), EventLog()
    s = Scheduler(clock, adapter, log, replay=True, uncalibrated=True)
    s.submit(make_scheduled_output(group_id=7, output=Output.NUT_025, t_send_ns=10))
    s.advance_to(10)
    recs = log.recent("send")
    assert len(recs) == 1
    r = recs[0]
    assert r["group_id"] == 7 and r["output"] == "nut_025"
    assert r["t_send_ns"] == 10 and r["t_actual_ns"] == 10 and r["miss"] == "none"
    assert r["timings"]["uncalibrated"] is True
    assert "UNCALIBRATED" in adapter.recent()[0]


def test_late_decision_sends_misc_immediately():
    clock, adapter, log = ManualClock(1_000), MockSerialAdapter(), EventLog()
    s = Scheduler(clock, adapter, log, replay=True)
    s.submit(make_scheduled_output(group_id=4, output=Output.BOLT_025, t_send_ns=500))
    assert len(adapter.sent) == 1
    sent, t_actual = adapter.sent[0]
    assert sent.output is Output.MISC and sent.t_send_ns == 500 and t_actual == 1_000
    r = log.recent("send")[0]
    assert r["miss"] == "late_decision" and r["output"] == "misc"
    assert s.records[0].miss is MissReason.LATE_DECISION and s.n_late == 1
    # exactly at the send time is not late
    s.submit(make_scheduled_output(group_id=5, t_send_ns=1_000))
    assert s.records[-1].miss is MissReason.NONE


def test_adapter_error_is_logged_not_raised():
    clock, log = ManualClock(0), EventLog()
    s = Scheduler(clock, MockSerialAdapter(fail_with=OSError("port")), log, replay=True)
    s.submit(make_scheduled_output(t_send_ns=5))
    s.advance_to(5)
    assert log.recent("send")[0]["miss"] == "adapter_error"


def test_replay_is_deterministic():
    def run():
        clock, log = ManualClock(0), EventLog()
        s = Scheduler(clock, MockSerialAdapter(), log, replay=True)
        rng = random.Random(1)
        for g in range(40):
            clock.advance_to(g * 50)
            s.advance_to(clock.now_ns())
            s.submit(make_scheduled_output(group_id=g, t_send_ns=g * 50 + rng.randint(-30, 400)))
        s.drain()
        return normalise_for_compare(log.recent())

    a, b = run(), run()
    assert a == b and len(a["send"]) == 40


def test_threaded_with_manual_clock():
    clock, adapter, log = ManualClock(0), MockSerialAdapter(), EventLog()
    s = Scheduler(clock, adapter, log, spin_ms=0)
    s.start()
    try:
        s.submit(make_scheduled_output(group_id=2, t_send_ns=2_000_000))
        s.submit(make_scheduled_output(group_id=1, t_send_ns=1_000_000))
        time.sleep(0.05)
        assert adapter.sent == []
        clock.advance_to(1_500_000)
        assert _wait(lambda: len(adapter.sent) == 1)
        clock.advance_to(5_000_000)
        assert _wait(lambda: len(adapter.sent) == 2)
        assert [o.group_id for o, _ in adapter.sent] == [1, 2]
    finally:
        s.stop()
    assert len(log.recent("send")) == 2


def test_adapters_satisfy_protocol_and_screen_keeps_last_n():
    assert isinstance(ScreenLogAdapter(), OutputAdapter)
    assert isinstance(MockSerialAdapter(), OutputAdapter)
    a = ScreenLogAdapter(keep=3)
    for g in range(5):
        a.send(make_scheduled_output(group_id=g), 2_000_000)
    assert len(a.recent()) == 3 and "group 4" in a.recent()[-1]
    m = MockSerialAdapter()
    m.close()
    assert m.closed


@pytest.mark.skipif(
    not os.environ.get("HWSORTER_RUN_JITTER"),
    reason="set HWSORTER_RUN_JITTER=1 to run the wall-clock jitter test",
)
def test_real_clock_jitter(capsys):
    clock, adapter, log = MonotonicClock(), MockSerialAdapter(), EventLog()
    s = Scheduler(clock, adapter, log, spin_ms=4.0)
    s.start()
    rng = random.Random(0)
    t = clock.now_ns() + 100 * NS_PER_MS
    try:
        for g in range(200):
            t += round(rng.uniform(5, 20) * NS_PER_MS)
            s.submit(make_scheduled_output(group_id=g, t_send_ns=t))
        assert _wait(lambda: len(adapter.sent) == 200, timeout=10)
    finally:
        s.stop()
    errs_ms = sorted((a - o.t_send_ns) / NS_PER_MS for o, a in adapter.sent)
    p50 = statistics.median(errs_ms)
    p99 = errs_ms[int(0.99 * len(errs_ms)) - 1]
    with capsys.disabled():
        print(f"\nJITTER send error ms: p50={p50:.3f} p99={p99:.3f} max={errs_ms[-1]:.3f}")
    assert (
        errs_ms[0] >= 0 and p99 < 10 and errs_ms[-1] < 25
    )  # target < 10 ms; max allows one OS preemption outlier
