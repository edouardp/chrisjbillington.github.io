# script to check if vaccination plots are out of date with respect to covidlive data.

import json
import requests
import numpy as np
from pathlib import Path

def latest_covidlive_date():
    """Return a np.datetime64 for the date covidlive most recently updated its
    vaccination data"""
    COVIDLIVE = 'https://covidlive.com.au/covid-live.json'
    covidlivedata = json.loads(requests.get(COVIDLIVE).content)

    STATES = ['AUS', 'NSW', 'VIC', 'SA', 'WA', 'TAS', 'QLD', 'NT', 'ACT']

    # We want the most recent date common to all jurisdictions
    maxdates = []
    for state in STATES:
        maxdate = max(
            np.datetime64(report['REPORT_DATE'])
            for report in covidlivedata
            if report['CODE'] == state and report['VACC_DOSE_CNT'] is not None
        )
        maxdates.append(maxdate)

    return min(maxdates)


def latest_html_update():
    """Return the Last updated date in aus_vaccinations.html as a np.datetime64"""
    html_file = 'aus_vaccinations.html'
    PREFIX = 'Last updated:' 
    for line in Path(html_file).read_text().splitlines():
        if PREFIX in line:
            return np.datetime64(line.replace(PREFIX, '').strip().split()[0])
    raise RuntimeError(f"update date not found in {html_file}")

if __name__ == '__main__':
    if latest_covidlive_date() > latest_html_update():
        print("outdated!")
    else:
        print("up to date!")
