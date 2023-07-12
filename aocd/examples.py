import argparse
import importlib.resources
import json
import logging
import re
import sys
from dataclasses import dataclass
from functools import cache
from itertools import zip_longest
from typing import NamedTuple

import bs4

from aocd.exceptions import ExampleParserError
from aocd.utils import get_plugins
from aocd.utils import _get_soup


log = logging.getLogger(__name__)


@dataclass
class Page:
    """
    Container of pre-parsed html to be used by example data extraction functions.

    Instances are expected to be initialised with the classmethod factory
    `Page.from_raw(html)` rather than created directly with Page(...).

    Every other attribute of the page is derived from the raw html.
    """

    raw_html: str  # String of the puzzle page html. May or may not have part b unlocked
    soup: bs4.BeautifulSoup  # The raw_html string parsed into a bs4.BeautifulSoup instance
    year: int  # AoC puzzle year (2015+) parsed from html title
    day: int  # AoC puzzle day (1-25) parsed from html title
    article_a: bs4.element.Tag  # The bs4 tag for the first <article> in the page, i.e. part a
    article_b: bs4.element.Tag  # The bs4 tag for the second <article> in the page, i.e. part b. It will be `None` if part b locked
    a_raw: str  # The first <article> html as a string
    b_raw: str  # The second <article> html as a string. Will be `None` if part b locked

    def __repr__(self):
        part_a_only = "*" if self.article_b is None else ""
        return f"<Page({self.year}, {self.day}){part_a_only} at {hex(id(self))}>"

    @classmethod
    def from_raw(cls, html):
        soup = _get_soup(html)
        title_pat = r"^Day (\d{1,2}) - Advent of Code (\d{4})$"
        title_text = soup.title.text
        if (match := re.match(title_pat, title_text)) is None:
            msg = f"failed to extract year/day from title {title_text!r}"
            raise ExampleParserError(msg)
        day, year = map(int, match.groups())
        articles = soup.find_all("article")
        if len(articles) == 0:
            raise ExampleParserError(f"no <article> found in html")
        elif len(articles) == 1:
            [article_a] = articles
            a_raw = str(article_a)
            article_b = b_raw = None
        elif len(articles) == 2:
            article_a, article_b = articles
            a_raw = str(article_a)
            b_raw = str(article_b)
        else:
            raise ExampleParserError(f"too many <article> found in html")
        page = Page(
            raw_html=html,
            soup=soup,
            year=year,
            day=day,
            article_a=article_a,
            article_b=article_b,
            a_raw=a_raw,
            b_raw=b_raw,
        )
        return page

    def __getattr__(self, name):
        if not name.startswith(("a_", "b_")):
            raise AttributeError(name)
        part, sep, tag = name.partition("_")
        if part == "b" and self.article_b is None:
            # hide part b accessors if part b is not unlocked yet
            raise AttributeError(name)
        if tag not in {"code", "li", "pre", "em"}:
            # only some soup attributes are whitelisted for access
            # these are computed dynamically and cached so that we
            # only pay the cost of parsing for them if/when they are
            # actually used by an example parser
            raise AttributeError(name)
        article = self.article_a if part == "a" else self.article_b
        if tag == "li":
            # list items usually need further drill-down
            result = article.find_all("li")
            for li in result:
                li.codes = [code.text for code in li.find_all("code")]
        else:
            result = [t.text for t in article.find_all(tag)]
        setattr(self, name, result)  # cache the result
        msg = "cached %s accessors for puzzle %d/%02d part %s page (%d hits)"
        log.debug(msg, tag, self.year, self.day, part, len(result))
        return result


class Example(NamedTuple):
    """
    Tuple of example data, answers, and any extra context needed for a solver.

    A list of these examples is returned by the `Puzzle.examples` property.
    User code should be able to run with the `example.input_data` and is expected
    to produce `example.answer_a` and `example.answer_b`.

    Sometimes examples in the prose need some extra context, such as a fewer
    number of iterations to be used when working with the test data. This may
    be returned as some human-readable string in `example.extra`
    """

    input_data: str
    answer_a: str = None
    answer_b: str = None
    extra: str = None

    @property
    def answers(self):
        return self.answer_a, self.answer_b


@cache
def _locators():
    # predetermined locations of code-blocks etc for example data
    resource = importlib.resources.files("aocd") / "examples.json"
    txt = resource.read_text()
    data = json.loads(txt)
    return data


def _trunc(s, maxlen=50):
    # don't print massive strings and mess up the table rendering
    if s is None or len(s) <= maxlen:
        return s
    return s[:maxlen] + f" ... ({len(s)} bytes)"


def extract_examples(html, use_default_locators=False):
    """
    Takes the puzzle page's raw html (str) and returns a list of `Example` instances.
    """
    page = Page.from_raw(html)
    scope = {"page": page}
    part_b_locked = page.article_b is None
    parts = "a" if part_b_locked else "ab"
    for part in parts:
        for tag in "code", "pre", "em", "li":
            name = f"{part}_{tag}"
            scope[name] = getattr(page, name)
    result = []
    locators = _locators()
    key = f"{page.year}/{page.day:02d}"
    default = locators["default_locators"]
    if use_default_locators:
        locs = [default]
    else:
        locs = locators.get(key, [default])
    for loc in locs:
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
            if isinstance(val, (tuple, list)):
                val = "\n".join(val)
            if val is not None:
                val = val.rstrip("\r\n")
            vals.append(val)
        if vals[0] is not None:
            result.append(Example(*vals))
    return result


def main():
    from aocd.models import Puzzle

    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        sys.exit(
            f"To use example parser, please install rich:\n"
            f"  {sys.executable} -m pip install rich"
        )
    eps = get_plugins(group="adventofcode.examples")
    plugins = {ep.name: ep for ep in eps}
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-p",
        "--plugin",
        choices=list(plugins),
        default="aocd_examples_canned",
        help="plugin to use for example extraction testing (default: %(default)s)",
    )
    parser.add_argument("-y", "--years", nargs="+", type=int, action="extend")
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        help="increased logging (may be specified multiple)",
    )
    args = parser.parse_args()
    if args.verbose is None:
        log_level = logging.WARNING
    elif args.verbose == 1:
        log_level = logging.INFO
    else:
        log_level = logging.DEBUG
    logging.basicConfig(level=log_level)
    years = args.years
    if not years:
        years = range(2015, 2023)
    if not plugins:
        print(
            "There are no plugins available. Install some package(s) "
            "with a registered 'adventofcode.examples' entry-point.\n"
            "See https://github.com/wimglenn/aocd-example-parser "
            "for a sample plugin package structure.",
            file=sys.stderr,
        )
        sys.exit(1)
    plugin = plugins[args.plugin].load()
    console = Console()

    wrong = []
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
            html = p._get_prose()
            page = Page.from_raw(html)
            part_b_locked = page.article_b is None
            scrapeds = plugin(page, [])
            corrects = p.examples
            if len(scrapeds) != len(corrects):
                msg = f"{year}/{day:02d} scraped {len(scrapeds)} but expected {len(corrects)}"
                log.info(msg)
            rows = enumerate(zip_longest(scrapeds, corrects, fillvalue=missing), 1)
            for i, (scraped, correct) in rows:
                row = [""] * 6
                if i == 1:
                    row[0] = f"{year}/{day:02d}"
                row[1] = str(i)
                if part_b_locked and day != 25:
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
                    wrong.append((year, day, i))

                row[3] = "❌✅"[i3] + f" {_trunc(scraped.answer_a)}"
                if not i3:
                    row[3] += f"\n(correct: {correct.answer_a})"
                    wrong.append((year, day, i))

                if day < 25 or scraped.answer_b:
                    row[4] = "❌✅"[i4] + f" {_trunc(scraped.answer_b)}"
                    if not i4:
                        row[4] += f"\n(correct: {correct.answer_b})"
                        wrong.append((year, day, i))
                if day < 25 and part_b_locked and i4:
                    row[4] = "❓"

                if scraped.extra or correct.extra:
                    row[5] = f"{scraped.extra or correct.extra or ''}"

                table.add_row(*row)
        console.print(table)
        print(f"the plugin {plugin} scored {score}/{total} ({score/total:.1%})")
