# policy web scraper <3

this tool searches uk gov and parliamentary sources for content mentioning china, then classifies each result by the type of policy framing used

runtime on all default sources is crazy, creates a csv with about 418 records - be warned.
---

## what it do ??

**collects article candidates** from a set of uk policy sources (gov.uk departments, Hansard, parliamentary committees, and research libraries)

---

## setup

**reqs:** python 3.9+

```bash
# create and activate a virtual environment - this is so your pc isn't all junked up with python lol
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# install dependencies else it won't run
pip install -r requirements.txt
```

---

## usage

```bash
# run against all default sources (outputs china_policy_scrape.csv)
python scraper.py

# if u wanna be fancy and name ur output file
python scraper.py --output my_results.csv

# if u hate my sources u can run against specific urls
python scraper.py https://www.gov.uk/government/organisations/home-office

```

---

## default sources

if no urls are provided scraper collects from:

| source | what it do |
|---|---|
| gov.uk / FCDO | Foreign, Commonwealth & Development Office publications |
| gov.uk / Cabinet Office | Cabinet Office publications |
| gov.uk / PM's Office | 10 Downing Street statements and publications |
| gov.uk / Ministry of Defence | MoD publications |
| Hansard | Parliamentary debates (Commons and Lords) |
| Intelligence and Security Committee | ISC reports and statements |
| Foreign Affairs Committee | Committee reports and evidence sessions |
| Defence Committee | Committee reports and evidence sessions |
| Joint Committee on the National Security Strategy | JCNSS reports |
| Commons Library | House of Commons research briefings |
| Lords Library | House of Lords research briefings |

(all nerd sites)

Gov.uk sources use the [gov.uk search API](https://www.gov.uk/api/search.json), pre-filtered for china-related content. parliamentary sources are scraped from their search and publications pages.

---

### core fields

| column | description |
|---|---|
| `url` | does what it says on the tin |
| `title` | does what it says on the tin |
| `publication_date` | does what it says on the tin |
| `department` | gov dept |
| `china_mentions` | does what it says on the tin |

### framing category columns

each `cat_*` column is a **count** of how many times keywords from that category appear in the article. A value of `0` means the framing is absent; higher values indicate stronger or more repeated use of that framing (framing is outlined using the vocab provided).

---

## results

**`china_mentions`** is a raw signal of how prominently China features in a document.

**category counts are not mutually exclusive.** a single document can score highly on both `cat_securitisation` and `cat_partnership`

## modifying keywords

All keyword lists are defined as constants at the top of [scraper.py](scraper.py):

- `CHINA_TERMS` — the primary filter; only documents matching these are included
- `KEYWORD_CATEGORIES` — a dict mapping each framing category name to its keyword list

add stuff to the right list don't touch anything else

---

## notes

- scraper waits 1 second before requests so we aren't classed as a bot and blocked
- some pages return a 403 (access denied) or other HTTP error. they r skipped with a log warning — ver normal thing can't have access to everything really
- pages that return a 403 or other HTTP error are skipped automatically