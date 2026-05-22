"""Regression tests for _build_po_structure — the function where the phantom
`4th Group` playoff games shipped for the [4,4,3,3] U12 layout.

The bug: it branched only on max_grp_size, so 3-team groups in a 4-group
division still emitted 4th-place games. These tests pin the correct bracket per
layout so a recurrence fails loudly.
"""
import pytest

from scheduler import _build_po_structure
from helpers import make_division


def build(div):
    # The second `groups` arg is unused by the function; pass None.
    return _build_po_structure([div], None)


def pairs_with_4th_group(games):
    """Every (lbl, t1, t2) tuple that references a '4th Group X' placeholder."""
    out = []
    for g in games:
        if "4th Group" in (g.get("t1") or "") or "4th Group" in (g.get("t2") or ""):
            out.append((g["lbl"], g.get("t1"), g.get("t2")))
    return out


def brackets(games):
    return {g.get("bracket") for g in games}


def labels(games):
    return [g["lbl"] for g in games]


# ── The exact regression: tiered 4-group layouts must NOT invent 4th-place games ──

def test_3333_is_tiered_with_no_4th_group_games():
    games = build(make_division("TEST 12", [3, 3, 3, 3]))
    assert len(games) == 12
    assert pairs_with_4th_group(games) == []  # the bug would add these
    assert brackets(games) == {
        "Championship (1st–4th)",
        "Silver (5th–8th)",
        "Bronze (9th–12th)",
    }
    # No 13-16 / 13th-place artifacts for a clean 12-team division.
    assert not any("13th" in l or "15th" in l for l in labels(games))


def test_4333_is_tiered_with_no_4th_group_games():
    """A4 (the single oversized-group runner-up) gets a training game elsewhere,
    NOT a bracket entry — so still zero '4th Group' games here."""
    games = build(make_division("TEST 13", [4, 3, 3, 3]))
    assert len(games) == 12
    assert pairs_with_4th_group(games) == []
    assert not any("13th" in l for l in labels(games))


def test_4433_has_exactly_one_13th_place_comp_game():
    games = build(make_division("TEST 14", [4, 4, 3, 3]))
    assert len(games) == 13  # 12 tiered + 1 compensation game
    # The ONLY 4th-group reference is the single 13th Place game, pairing the
    # two oversized groups' 4th-placers (groups A and B are the size-4 ones).
    comp = [g for g in games if g["lbl"] == "13th Place"]
    assert len(comp) == 1
    assert comp[0]["t1"] == "4th Group A"
    assert comp[0]["t2"] == "4th Group B"
    assert comp[0]["bracket"] == "13th Place"
    assert pairs_with_4th_group(games) == [("13th Place", "4th Group A", "4th Group B")]


def test_4444_uses_paired_silver_bronze_and_13_16():
    games = build(make_division("TEST 16", [4, 4, 4, 4]))
    assert len(games) == 10
    # Here 4th-group games ARE legitimate (the 13-16 placement bracket).
    refs = pairs_with_4th_group(games)
    assert ("13th/14th", "4th Group A", "4th Group B") in refs
    assert ("15th/16th", "4th Group C", "4th Group D") in refs
    assert "13th–16th Place" in brackets(games)
    # Paired (not tiered): Silver/Bronze have no semifinals here.
    silver_bronze = [g for g in games
                     if g.get("bracket") in ("Silver (5th–8th)", "Bronze (9th–12th)")]
    assert all(not g["lbl"].startswith("SF") for g in silver_bronze)


# ── The smaller layouts, pinned for completeness ──

def test_single_group_is_one_final():
    games = build(make_division("TEST 4", [4]))
    assert len(games) == 1
    assert games[0]["lbl"] == "FINAL"
    assert games[0]["t1"] == "1st Group A"
    assert games[0]["t2"] == "2nd Group A"


def test_two_groups_of_5_direct_seed():
    games = build(make_division("TEST 10", [5, 5]))
    assert sorted(labels(games)) == ["FINAL", "Semi Final"]


def test_two_groups_of_3_has_5th_but_no_7th_or_4th_group():
    games = build(make_division("TEST 6", [3, 3]))
    assert len(games) == 5  # SF1, SF2, 3rd, FINAL, 5th Place
    assert "5th Place" in labels(games)
    assert "7th Place" not in labels(games)
    assert pairs_with_4th_group(games) == []


def test_two_groups_of_4_has_5th_and_7th():
    games = build(make_division("TEST 8", [4, 4]))
    assert len(games) == 6  # SF1, SF2, 3rd, FINAL, 5th, 7th
    seventh = [g for g in games if g["lbl"] == "7th Place"]
    assert len(seventh) == 1
    assert seventh[0]["t1"] == "4th Group A"
    assert seventh[0]["t2"] == "4th Group B"


def test_u18_girls_two_group_uses_crossover_semis():
    """Name-driven branch: U18 GIRLS pair their SFs 1A-2B / 1B-2A."""
    games = build(make_division("U18 GIRLS", [4, 4]))
    sf1 = next(g for g in games if g["lbl"] == "SF 1")
    sf2 = next(g for g in games if g["lbl"] == "SF 2")
    assert (sf1["t1"], sf1["t2"]) == ("1st Group A", "2nd Group B")
    assert (sf2["t1"], sf2["t2"]) == ("1st Group B", "2nd Group A")


def test_multiple_divisions_are_all_emitted():
    games = _build_po_structure(
        [make_division("TEST 12", [3, 3, 3, 3]), make_division("TEST 16", [4, 4, 4, 4])],
        None,
    )
    names = {g["divName"] for g in games}
    assert names == {"TEST 12", "TEST 16"}
    assert len(games) == 12 + 10
