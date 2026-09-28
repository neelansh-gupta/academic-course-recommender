"""Timetable clash checks over stored section rows."""

from bitsrec.schedule.clashes import assess_sections, compare_sections, feasible_sections, find_clashes
from bitsrec.schedule.hours import hours_for_clock, load_hour_legend
from bitsrec.schedule.select import choose_among, select_section

__all__ = [
    "assess_sections",
    "choose_among",
    "compare_sections",
    "feasible_sections",
    "find_clashes",
    "hours_for_clock",
    "load_hour_legend",
    "select_section",
]
