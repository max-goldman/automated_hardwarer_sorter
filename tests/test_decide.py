"""Grouping and decision rules (task 009)."""

import pytest
from fakes import make_part_result

from hwsorter.contracts import RULES, Output, PartClass, TrackFlag, Verdict
from hwsorter.decide import Decider, Grouper, correct_output, decide
from hwsorter.decide.rules import decide_with_rule

B, N, W = PartClass.BOLT_025, PartClass.NUT_025, PartClass.WASHER_0312
CONF, OOS, UNR = Verdict.CONFIDENT, Verdict.OUT_OF_SCOPE, Verdict.UNRESOLVED


def part(tid, verdict=CONF, cls=B, trail=0.0, lead=0.5, **kw):
    if verdict is not CONF:
        cls = None
    return make_part_result(
        track_id=tid,
        verdict=verdict,
        part_class=cls,
        belt_x_trail_in=trail,
        belt_x_lead_in=lead,
        **kw,
    )


# ---- decision table rows
def test_row_one_in_scope():
    assert decide([part(1, cls=N)]) is Output.NUT_025


def test_row_one_oos():
    assert decide([part(1, OOS)]) is Output.MISC


def test_row_same_class_group():
    assert decide([part(1), part(2), part(3)]) is Output.BOLT_025


def test_row_mixed_classes():
    assert decide([part(1, cls=B), part(2, cls=N)]) is Output.REWORK


def test_row_in_scope_plus_oos():
    assert decide([part(1), part(2, OOS)]) is Output.REWORK


def test_row_only_oos():
    assert decide([part(1, OOS), part(2, OOS)]) is Output.MISC


# ---- edge cases of decision
def test_lone_unresolved_is_misc():
    assert decide_with_rule([part(1, UNR)]) == (Output.MISC, "lone_misc")


def test_group_with_unresolved_is_rework():
    assert decide([part(1), part(2, UNR)]) is Output.REWORK
    assert decide([part(1, OOS), part(2, UNR)]) is Output.REWORK


def test_merged_flag_member_unresolved_is_rework():
    m = part(2, UNR, flags=TrackFlag.MERGED)
    assert decide([part(1), m]) is Output.REWORK


def test_rules_ids():
    cases = {
        "lone_confident": [part(1)],
        "lone_misc": [part(1, OOS)],
        "group_same_class": [part(1), part(2)],
        "group_all_oos": [part(1, OOS), part(2, OOS)],
        "rework": [part(1), part(2, cls=N)],
    }
    assert set(cases) == set(RULES)
    for rule, members in cases.items():
        assert decide_with_rule(members)[1] == rule


def test_decide_empty_raises():
    with pytest.raises(ValueError):
        decide([])


# ---- correct_output
@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        ([B], Output.BOLT_025),
        ([None], Output.MISC),
        ([W, W, W], Output.WASHER_0312),
        ([B, N], Output.REWORK),
        ([B, None], Output.REWORK),
        ([None, None], Output.MISC),
    ],
)
def test_correct_output(labels, expected):
    assert correct_output(labels) is expected


def test_correct_output_empty_raises():
    with pytest.raises(ValueError):
        correct_output([])


# ---- grouping
def ids(groups):
    return [[p.track_id for p in g] for g in groups]


def test_side_by_side_gap_zero_linked():
    g = Grouper(1.0)
    g.add(part(1, trail=2.0, lead=2.5))
    g.add(part(2, trail=2.0, lead=2.5))
    assert ids(g.flush()) == [[1, 2]]


def test_chain_links_a_c_beyond_gap():
    g = Grouper(1.0)
    g.add(part(1, trail=0.0, lead=0.5))
    g.add(part(2, trail=1.0, lead=1.5))  # gap A-B 0.5
    g.add(part(3, trail=2.0, lead=2.5))  # gap B-C 0.5, A-C 1.5
    assert ids(g.flush()) == [[1, 2, 3]]


def test_chain_order_independent_of_add_order():
    g = Grouper(1.0)
    for p in (
        part(3, trail=2.0, lead=2.5),
        part(1, trail=0.0, lead=0.5),
        part(2, trail=1.0, lead=1.5),
    ):
        g.add(p)
    assert ids(g.flush()) == [[1, 2, 3]]


def test_long_part_bridges_short_neighbours():
    g = Grouper(1.0)
    g.add(part(1, trail=0.0, lead=5.0))
    g.add(part(2, trail=1.0, lead=1.2))  # nested inside 1
    g.add(part(3, trail=5.5, lead=6.0))  # gap 0.5 from lead of 1
    assert ids(g.flush()) == [[1, 2, 3]]


def test_exactly_at_gap_not_linked():
    g = Grouper(1.0)
    g.add(part(1, trail=0.0, lead=0.5))
    g.add(part(2, trail=1.5, lead=2.0))  # gap exactly 1.0
    assert ids(g.flush()) == [[2], [1]]


def test_just_under_gap_linked():
    g = Grouper(1.0)
    g.add(part(1, trail=0.0, lead=0.5))
    g.add(part(2, trail=1.49, lead=2.0))
    assert ids(g.flush()) == [[1, 2]]


def test_closure_only_after_gap_of_empty_belt():
    g = Grouper(1.0)
    g.add(part(1, trail=5.0, lead=5.5))
    assert g.advance(5.0, 0) == []  # belt entering is at the trail edge
    assert g.advance(4.5, 0) == []
    assert g.advance(4.01, 0) == []
    assert ids(g.advance(4.0, 0)) == [[1]]  # exactly rework_gap behind trail
    assert g.pending() == 0


def test_closure_uses_group_trail_and_waits_for_chain():
    g = Grouper(1.0)
    g.add(part(1, trail=5.0, lead=5.5))
    g.add(part(2, trail=6.0, lead=6.5))
    assert g.advance(4.5, 0) == []
    assert ids(g.advance(4.0, 0)) == [[1, 2]]


def test_advance_closes_only_ready_groups_downstream_first():
    g = Grouper(1.0)
    for p in (
        part(1, trail=10.0, lead=10.5),
        part(2, trail=20.0, lead=20.5),
        part(3, trail=3.0, lead=3.5),
    ):
        g.add(p)
    assert ids(g.advance(9.0, 0)) == [[2], [1]]
    assert g.pending() == 1
    assert ids(g.flush()) == [[3]]
    assert g.flush() == []


def test_flush_closes_all():
    g = Grouper(1.0)
    g.add(part(1, trail=10.0, lead=10.5))
    g.add(part(2, trail=20.0, lead=20.5))
    assert ids(g.flush()) == [[2], [1]]


# ---- decider
def test_decider_group_fields_from_most_downstream_member():
    d = Decider(1.0)
    d.add(part(1, trail=5.0, lead=5.5, t_ref_ns=100, x_lead_in_at_ref=1.0))
    d.add(part(2, trail=5.8, lead=6.4, t_ref_ns=200, x_lead_in_at_ref=2.0))
    d.add(part(3, trail=5.2, lead=5.9, t_ref_ns=300, x_lead_in_at_ref=3.0))
    (g,) = d.advance(4.0, 999)
    assert g.member_track_ids == (1, 2, 3)
    assert (g.t_ref_ns, g.x_lead_in_at_ref, g.t_closed_ns) == (200, 2.0, 999)
    assert g.output is Output.BOLT_025
    assert g.rule == "group_same_class"


def test_decider_group_ids_monotonic_deterministic():
    def run():
        d = Decider(1.0)
        d.add(part(1, trail=10.0, lead=10.5))
        d.add(part(2, OOS, trail=20.0, lead=20.5))
        first = d.advance(9.0, 1)
        d.add(part(3, trail=2.0, lead=2.5, cls=N))
        return first + d.flush(2)

    a, b = run(), run()
    assert a == b
    assert [g.group_id for g in a] == [1, 2, 3]
    assert [g.member_track_ids for g in a] == [(2,), (1,), (3,)]
    assert [g.output for g in a] == [Output.MISC, Output.BOLT_025, Output.NUT_025]
    assert a[0].rule == "lone_misc"


def test_all_oos_group_misc_via_decider():
    d = Decider(1.0)
    d.add(part(1, OOS, trail=0.0, lead=0.3))
    d.add(part(2, OOS, trail=0.5, lead=0.9))
    (g,) = d.flush(0)
    assert (g.output, g.rule) == (Output.MISC, "group_all_oos")
