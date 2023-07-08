import argparse
import importlib.resources
import json
import logging
import re
import sys
from functools import cache
from itertools import zip_longest
from typing import NamedTuple

import bs4

from aocd.exceptions import ExampleParserError
from aocd.utils import _get_soup


log = logging.getLogger(__name__)


class Page(NamedTuple):
    year: int
    day: int
    raw_html: str
    soup: bs4.BeautifulSoup
    a: bs4.element.Tag
    a_raw: str
    b: bs4.element.Tag
    b_raw: str

    def __repr__(self):
        return f"<Page({self.year}, {self.day}) at {hex(id(self))}>"

    @classmethod
    def from_raw(cls, html):
        soup = _get_soup(html)
        title_pat = r"^Day (\d{1,2}) - Advent of Code (\d{4})$"
        title_text = soup.title.text
        if (match := re.match(title_pat, title_text)) is None:
            raise ExampleParserError(f"failed to extract year/day from title {title_text!r}")
        day, year = map(int, match.groups())
        articles = soup.find_all('article')
        if len(articles) == 0:
            raise ExampleParserError(f"no <article> found in html")
        elif len(articles) == 1:
            [a] = articles
            a_raw = str(a)
            b = b_raw = None
        elif len(articles) == 2:
            a, b = articles
            a_raw = str(a)
            b_raw = str(b)
        else:
            raise ExampleParserError(f"too many <article> found in html")
        return Page(year=year, day=day, raw_html=html, soup=soup, a=a, a_raw=a_raw, b=b, b_raw=b_raw)

    @property
    def ca(self):
        return [code.text for code in self.a.find_all('code')]

    @property
    def cb(self):
        if self.b is None:
            raise AttributeError("cb")
        return [code.text for code in self.b.find_all('code')]


class Example(NamedTuple):
    input_data: str
    answer_a: str = None
    answer_b: str = None
    extra: str = None

    @property
    def answers(self):
        return self.answer_a, self.answer_b


def get_actual(year, day):
    examples = []
    from pathlib import Path
    path = Path(f"~/git/advent-of-code-wim/tests/{year}/{day:02d}/").expanduser()
    for p in sorted(path.glob("*.txt")):
        blacklist = "broken", "jwolf", "fizbin", "_wim", "_topaz", "_reddit", "_wim"
        if any(s in p.name for s in blacklist):
            continue
        with p.open() as f:
            lines = list(f)
        input_data = "".join(lines[:-2]).rstrip("\r\n")
        answer_a = lines[-2].split("#")[0].strip()
        answer_b = lines[-1].split("#")[0].strip()
        if answer_a == "-":
            answer_a = None
        if answer_b == "-":
            answer_b = None
        example = Example(input_data, answer_a, answer_b)
        examples.append(example)
    return examples


@cache
def _locators():
    resource = importlib.resources.files("aocd") / "examples.json"
    txt = resource.read_text()
    data = json.loads(txt)
    return data


def _trunc(s, maxlen=50):
    if s is None or len(s) <= maxlen:
        return s
    return s[:maxlen] + f" ... ({len(s)} bytes)"


def extract_examples(html):
    page = Page.from_raw(html)
    scope = {"soup": page.soup}
    part_b_locked = page.b is None
    result = []
    locators = _locators()
    key = f"{page.year}/{page.day:02d}"
    default = locators["default_locators"]
    for loc in locators.get(key, [default]):
        vals = []
        for k in "input_data", "answer_a", "answer_b", "extra":
            pos = loc.get(k, default[k])
            if k == "extra" and pos is None:
                break
            if k == "answer_b" and (part_b_locked or page.day == 25):
                vals.append(None)
                continue
            try:
                val = eval(pos, scope)
            except Exception:
                val = None
            if val is not None:
                val = val.rstrip("\r\n")
            vals.append(val)
        if vals[0] is not None:
            result.append(Example(*vals))
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from aocd.models import Puzzle
    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        sys.exit(
            f"To use example parser, please install rich:\n"
            f"  {sys.executable} -m pip install rich"
        )
    parser = argparse.ArgumentParser()
    parser.add_argument("-y", "--years", nargs="+", type=int, action="extend")
    args = parser.parse_args()
    years = args.years
    if not years:
        years = range(2015, 2023)
    console = Console()

    for year in years:
        score = total = 0
        table = Table(title=f"Advent of Code examples for year {year}")
        table.add_column("YYYY/DD", style="cyan")
        table.add_column("eg")
        table.add_column("Example data")
        table.add_column("Part A answer")
        table.add_column("Part B answer")
        table.add_column("Extra")
        missing = Example("")
        for day in range(1, 26):
            p = Puzzle(year, day)
            part_b_locked = len(_get_soup(p._get_prose()).find_all("article")) != 2
            scrapeds = p.examples
            corrects = get_actual(year, day)
            if len(scrapeds) > len(corrects):
                log.warning(f"{year}/{day:02d} scraped {len(scrapeds)} but expected {len(corrects)}")
            for i, (scraped, correct) in enumerate(zip_longest(scrapeds, corrects, fillvalue=missing), start=1):
                row = [""] * 6
                if i == 1:
                    row[0] = f"{year}/{day:02d}"
                row[1] = str(i)
                if part_b_locked:
                    row[1] += "(a)"

                i2 = scraped.input_data == correct.input_data
                i3 = scraped.answer_a == correct.answer_a
                if part_b_locked:
                    i4 = scraped.answer_b is None
                else:
                    i4 = scraped.answer_b == correct.answer_b
                i5 = scraped.extra == correct.extra

                row[2] = "❌✅"[i2] + f" ({len(scraped.input_data or '')} bytes)"
                if not i2:
                    row[2] += f"\n(correct: {len(correct.input_data or '')} bytes)"

                row[3] = "❌✅"[i3] + f" {_trunc(scraped.answer_a)}"
                if not i3:
                    row[3] += f"\n(correct: {correct.answer_a})"

                if day < 25 or scraped.answer_b:
                    row[4] = "❌✅"[i4] + f" {_trunc(scraped.answer_b)}"
                    if not i4:
                        row[4] += f"\n(correct: {correct.answer_b})"
                if day < 25 and part_b_locked and i4:
                    row[4] = "❓"

                if scraped.extra or correct.extra:
                    row[5] = f"{scraped.extra or correct.extra or ''}"

                table.add_row(*row)
        console.print(table)
