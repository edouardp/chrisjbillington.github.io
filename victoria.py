import os
from datetime import datetime
from pytz import timezone
from pathlib import Path
import io
import zipfile
import tempfile
import subprocess
import html

from scipy.optimize import curve_fit
from scipy.signal import convolve
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.units as munits
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import matplotlib.colors as mcolors
import pandas as pd
import pantab
import requests

converter = mdates.ConciseDateConverter()

munits.registry[np.datetime64] = converter
munits.registry[datetime.date] = converter
munits.registry[datetime] = converter


def gaussian_smoothing(data, pts):
    """gaussian smooth an array by given number of points"""
    x = np.arange(-4 * pts, 4 * pts + 1, 1)
    kernel = np.exp(-(x ** 2) / (2 * pts ** 2))
    smoothed = convolve(data, kernel, mode='same')
    normalisation = convolve(np.ones_like(data), kernel, mode='same')
    return smoothed / normalisation


def fourteen_day_average(data):
    ret = np.cumsum(data, dtype=float)
    ret[14:] = ret[14:] - ret[:-14]
    return ret / 14


def partial_derivatives(function, x, params, u_params):
    model_at_center = function(x, *params)
    partial_derivatives = []
    for i, (param, u_param) in enumerate(zip(params, u_params)):
        d_param = u_param / 1e6
        params_with_partial_differential = np.zeros(len(params))
        params_with_partial_differential[:] = params[:]
        params_with_partial_differential[i] = param + d_param
        model_at_partial_differential = function(x, *params_with_partial_differential)
        partial_derivative = (model_at_partial_differential - model_at_center) / d_param
        partial_derivatives.append(partial_derivative)
    return partial_derivatives


def model_uncertainty(function, x, params, covariance):
    u_params = [np.sqrt(abs(covariance[i, i])) for i in range(len(params))]
    derivs = partial_derivatives(function, x, params, u_params)
    squared_model_uncertainty = sum(
        derivs[i] * derivs[j] * covariance[i, j]
        for i in range(len(params))
        for j in range(len(params))
    )
    return np.sqrt(squared_model_uncertainty)


url = "https://public.tableau.com/workbooks/Cases_15982342702770.twb"
dbname = "Data/Extracts/federated_12gagec10ajljj1457q361.hyper"
workbook_data = requests.get(url).content
workbook = zipfile.ZipFile(io.BytesIO(workbook_data))
with tempfile.TemporaryDirectory() as tempdir:
    dbpath = workbook.extract(dbname, path=tempdir)
    name, df = pantab.frames_from_hyper(dbpath).popitem()

data = []
for cases, date in zip(df['Cases'], df['Date']):
    try:
        cases = float(cases)
    except TypeError:
        cases = 0
    date = np.datetime64(date, 'h') + 24
    data.append((date, cases))

data.sort()
dates, new = [np.array(a) for a in zip(*data)]

# Fill in missing dates when there were zero cases:
for date in np.arange(dates[0], dates[-1], 24):
    if date not in dates:
        print("MISSING DATE:", date)
        data.append((date, 0))

data.sort()
dates, new = [np.array(a) for a in zip(*data)]

new[np.isnan(new)] = 0

# def read_DHHS_new(page):
#     df = pd.read_html(page)[3]
#     dates = np.array(
#         [
#             np.datetime64(datetime.strptime(d + ' 2020', "%d/%m %Y"), 'h') + 24
#             for d in df['Date'][:-1]
#         ]
#     )
#     new = np.array(df['Total daily confirmed cases'][:-1], dtype=int)
#     return dates[::-1], new[::-1]

def read_DHHS_unknowns(page):
    data = Path('DHHS-unknowns.txt').read_text()
    # latest_mysteries = pd.read_html(page)[1]['Overall'][0]
    # datestr = page.split("For the last 14 days")[-1].split("– ")[1].split(")")[0]
    # datestr = html.unescape(datestr)
    # latest_date = np.datetime64(datetime.strptime(datestr, "%d %b %Y"), 'h')

    dates = []
    mysteries = []
    for line in data.splitlines():
        if not line.strip():
            continue
        date, cases = line.split()
        dates.append(np.datetime64(date, 'h'))
        mysteries.append(int(cases))

    # if dates[-1] != latest_date:
    #     dates.append(latest_date)
    #     mysteries.append(int(latest_mysteries))
    #     data += f"{str(latest_date).split('T')[0]} {latest_mysteries}\n"
    #     Path('DHHS-unknowns.txt').write_text(data)

    return np.array(dates), np.array(mysteries)

url = "https://www.dhhs.vic.gov.au/averages-easing-restrictions-covid-19"
page = requests.get(url).text
# last_14d_dates, new_last_14d = read_DHHS_new(page)
unknowns_last_14d_dates, unknowns_last_14d = read_DHHS_unknowns(page)

# If main dataset not yet updated today, use data from DHHS averages page for the last 14
# days - it will include recent reclassifications and today's number:
# if dates[-1] != last_14d_dates[-1]:
#     dates = np.append(dates, [last_14d_dates[-1]])
#     new = np.append(new, [new_last_14d[-1]])
#     new[-14:] = new_last_14d


LAST_DATE = np.datetime64('2020-11-15T00', 'h')

new = new[dates <= LAST_DATE]
dates = dates[dates <= LAST_DATE]

START_IX = 35

all_new = new
all_dates = dates

ANIMATE = False


if ANIMATE:
    os.makedirs('VIC-animated', exist_ok=True)
    LOOP_START = START_IX + 10
else:
    LOOP_START = len(dates)

for j in range(LOOP_START, len(dates) + 1):
    dates = all_dates[:j]
    new = all_new[:j]

    SMOOTHING = 4
    new_padded = np.zeros(len(new) + 3 * SMOOTHING)
    new_padded[: -3 * SMOOTHING] = new


    def exponential(x, A, k):
        return A * np.exp(k * x)

    tau = 5  # reproductive time of the virus in days

    # Smoothing requires padding to give sensible results at the right edge. Compute an
    # exponential fit to daily cases over the last fortnight, and pad the data with the
    # fit results prior to smoothing.

    FIT_PTS = 20
    x0 = -14
    delta_x = 1
    fit_x = np.arange(-FIT_PTS, 0)
    fit_weights = 1 / (1 + np.exp(-(fit_x - x0) / delta_x))
    pad_x = np.arange(3 * SMOOTHING)

    def clip_params(params):
        # Clip exponential fil params to be within a reasonable range to suppress when
        # unlucky points lead us to an unrealistic exponential blowup. Mofiedies array
        # in-place.
        R_CLIP = 5 # Limit the exponential fits to a maximum of R=5
        params[0] = min(params[0], 2 * new[-FIT_PTS:].max() + 1)
        params[1] = min(params[1], np.log(R_CLIP ** (1 / tau)))

    params, cov = curve_fit(exponential, fit_x, all_new[-FIT_PTS:], sigma=1/fit_weights)
    clip_params(params)
    fit = exponential(pad_x, *params).clip(0.1, None)
    all_new_padded = np.zeros(len(all_new) + 3 * SMOOTHING)
    all_new_padded[: -3 * SMOOTHING] = all_new
    all_new_padded[-3 * SMOOTHING :] = fit
    all_new_smoothed = gaussian_smoothing(all_new_padded, SMOOTHING)[: -3 * SMOOTHING]

    params, cov = curve_fit(exponential, fit_x, new[-FIT_PTS:], sigma=1/fit_weights)
    clip_params(params)
    fit = exponential(pad_x, *params).clip(0.1, None)
    new_padded[-3 * SMOOTHING :] = fit
    new_smoothed = gaussian_smoothing(new_padded, SMOOTHING)[: -3 * SMOOTHING]
    R = (new_smoothed[1:] / new_smoothed[:-1]) ** tau

    N_monte_carlo = 1000
    variance_R = np.zeros_like(R)
    variance_new_smoothed = np.zeros_like(new_smoothed)
    cov_R_new_smoothed = np.zeros_like(R)
    # Monte-carlo of the above with noise to compute variance in R, new_smoothed,
    # and their covariance:
    u_new = np.sqrt((0.2 * new) ** 2 + new)  # sqrt(N) and 20%, added in quadrature
    for i in range(N_monte_carlo):
        new_with_noise = np.random.normal(new, u_new).clip(0.1, None)
        params, cov = curve_fit(
            exponential,
            fit_x,
            new_with_noise[-FIT_PTS:],
            sigma=1 / fit_weights,
            maxfev=20000,
        )
        clip_params(params)
        scenario_params = np.random.multivariate_normal(params, cov)
        clip_params(scenario_params)
        fit = exponential(pad_x, *scenario_params).clip(0.1, None)
        new_padded[: -3 * SMOOTHING] = new_with_noise
        new_padded[-3 * SMOOTHING :] = fit
        new_smoothed_noisy = gaussian_smoothing(new_padded, SMOOTHING)[: -3 * SMOOTHING]
        variance_new_smoothed += (new_smoothed_noisy - new_smoothed) ** 2 / N_monte_carlo
        R_noisy = (new_smoothed_noisy[1:] / new_smoothed_noisy[:-1]) ** tau
        variance_R += (R_noisy - R) ** 2 / N_monte_carlo
        cov_R_new_smoothed += (new_smoothed_noisy[1:] - new_smoothed[1:]) * (R_noisy - R) / N_monte_carlo

    u_R = np.sqrt(variance_R)
    R_upper = R + u_R
    R_lower = R - u_R

    u_new_smoothed = np.sqrt(variance_new_smoothed)
    new_smoothed_upper = new_smoothed + u_new_smoothed
    new_smoothed_lower = new_smoothed - u_new_smoothed

    R_upper = R_upper.clip(0, 10)
    R_lower = R_lower.clip(0, 10)
    R = R.clip(0, None)

    new_smoothed_upper = new_smoothed_upper.clip(0, None)
    new_smoothed_lower = new_smoothed_lower.clip(0, None)
    new_smoothed = new_smoothed.clip(0, None)

    START_PLOT = np.datetime64('2020-03-01', 'h')
    END_PLOT = np.datetime64('2020-12-31', 'h')

    # Propagate uncertainty in log space where linear uncertainty propagation better
    # applies
    def log_projection_model(t, A, R):
        return np.log(A * R ** (t / tau))

    # Projection of daily case numbers:
    days_projection = (END_PLOT - dates[-1]).astype(int) // 24
    t_projection = np.linspace(0, days_projection, days_projection + 1)

    # Construct a covariance matrix for the latest estimate in new_smoothed and R:
    cov = np.array(
        [
            [variance_new_smoothed[-1], cov_R_new_smoothed[-1]],
            [cov_R_new_smoothed[-1], variance_R[-1]],
        ]
    )

    new_projection = np.exp(log_projection_model(t_projection, new_smoothed[-1], R[-1]))
    log_new_projection_uncertainty = model_uncertainty(
        log_projection_model, t_projection, (new_smoothed[-1], R[-1]), cov
    )
    new_projection_upper = np.exp(np.log(new_projection) + log_new_projection_uncertainty)
    new_projection_lower = np.exp(np.log(new_projection) - log_new_projection_uncertainty)

    # # Examining whether the smoothing and uncertainty look decent
    # plt.bar(dates, new)
    # plt.fill_between(
    #     dates,
    #     new_smoothed_lower,
    #     new_smoothed_upper,
    #     color='orange',
    #     alpha=0.5,
    #     zorder=5,
    #     linewidth=0,
    # )
    # plt.plot(dates, new_smoothed, color='orange', zorder=6)
    # plt.plot(
    #     dates[-1] + 24 * t_projection.astype('timedelta64[h]'),
    #     new_projection,
    #     color='orange',
    #     zorder=6,
    # )
    # plt.fill_between(
    #     dates[-1] + 24 * t_projection.astype('timedelta64[h]'),
    #     new_projection_lower,
    #     new_projection_upper,
    #     color='orange',
    #     alpha=0.5,
    #     zorder=5,
    #     linewidth=0,
    # )
    # plt.grid(True)
    # plt.axis(
    #     xmin=np.datetime64('2020-06-15', 'h'), xmax=np.datetime64('2020-10-01', 'h')
    # )


    STAGE_ONE = np.datetime64('2020-03-23', 'h')
    STAGE_TWO = np.datetime64('2020-03-26', 'h')
    STAGE_THREE = np.datetime64('2020-03-31', 'h')
    STAGE_TWO_II = np.datetime64('2020-06-01', 'h')
    POSTCODE_STAGE_3 = np.datetime64('2020-07-02', 'h')
    STAGE_THREE_II = np.datetime64('2020-07-08', 'h')
    MASKS = np.datetime64('2020-07-23', 'h')
    STAGE_FOUR = np.datetime64('2020-08-02', 'h')
    FIRST_STEP = np.datetime64('2020-09-14', 'h')
    SECOND_STEP = np.datetime64('2020-09-28', 'h')
    STEP_TWO_POINT_FIVE = np.datetime64('2020-10-19', 'h')
    THIRD_STEP = np.datetime64('2020-10-28', 'h')
    LAST_STEP = np.datetime64('2020-11-23', 'h')
    COVID_SAFE_SUMMER = np.datetime64('2020-12-07', 'h')

    ORANGEYELLOW = (
        np.array(mcolors.to_rgb("orange")) + np.array(mcolors.to_rgb("yellow"))
    ) / 2

    fig1 = plt.figure(figsize=(18, 6))
    plt.fill_betweenx(
        [-10, 10],
        [STAGE_ONE, STAGE_ONE],
        [STAGE_TWO, STAGE_TWO],
        color="green",
        alpha=0.5,
        linewidth=0,
        label="Stage 1 / Last step",
    )

    plt.fill_betweenx(
        [-10, 10],
        [STAGE_TWO, STAGE_TWO],
        [STAGE_THREE, STAGE_THREE],
        color="yellow",
        alpha=0.5,
        linewidth=0,
        label="Stage 2 / Third step",
    )

    plt.fill_betweenx(
        [-10, 10],
        [STAGE_THREE, STAGE_THREE],
        [STAGE_TWO_II, STAGE_TWO_II],
        color="orange",
        alpha=0.5,
        linewidth=0,
        label="Stage 3 / Second step",
    )

    plt.fill_betweenx(
        [-10, 10],
        [STAGE_TWO_II, STAGE_TWO_II],
        [POSTCODE_STAGE_3, POSTCODE_STAGE_3],
        color="yellow",
        alpha=0.5,
        linewidth=0,
    )


    plt.fill_betweenx(
        [-10, 10],
        [POSTCODE_STAGE_3, POSTCODE_STAGE_3],
        [STAGE_THREE_II, STAGE_THREE_II],
        color="yellow",
        edgecolor="orange",
        alpha=0.5,
        linewidth=0,
        hatch="//////",
        label="Postcode Stage 3",
    )


    plt.fill_betweenx(
        [-10, 10],
        [STAGE_THREE_II, STAGE_THREE_II],
        [MASKS, MASKS],
        color="orange",
        alpha=0.5,
        linewidth=0,
    )

    plt.fill_betweenx(
        [-10, 10],
        [MASKS, MASKS],
        [STAGE_FOUR, STAGE_FOUR],
        color="orange",
        edgecolor="red",
        alpha=0.5,
        linewidth=0,
        hatch="//////",
        label="Masks introduced",
    )


    plt.fill_betweenx(
        [-10, 10],
        [STAGE_FOUR, STAGE_FOUR],
        [SECOND_STEP, SECOND_STEP],
        color="red",
        alpha=0.5,
        linewidth=0,
        label="Stage 4 / First step",
    )

    plt.fill_betweenx(
        [-10, 10],
        [SECOND_STEP, SECOND_STEP],
        [STEP_TWO_POINT_FIVE, STEP_TWO_POINT_FIVE],
        color="orange",
        alpha=0.5,
        linewidth=0,
    )

    plt.fill_betweenx(
        [-10, 10],
        [STEP_TWO_POINT_FIVE, STEP_TWO_POINT_FIVE],
        [THIRD_STEP, THIRD_STEP],
        color=ORANGEYELLOW,
        alpha=0.5,
        linewidth=0,
        label="Step 2.5"
    )

    plt.fill_betweenx(
        [-10, 10],
        [THIRD_STEP, THIRD_STEP],
        [LAST_STEP, LAST_STEP],
        color="yellow",
        alpha=0.5,
        linewidth=0,
    )

    plt.fill_betweenx(
        [-10, 10],
        [LAST_STEP, LAST_STEP],
        [COVID_SAFE_SUMMER, COVID_SAFE_SUMMER],
        color="green",
        alpha=0.5,
        linewidth=0,
    )

    plt.fill_betweenx(
        [-10, 10],
        [COVID_SAFE_SUMMER, COVID_SAFE_SUMMER],
        [END_PLOT, END_PLOT],
        color="green",
        # edgecolor="green",
        alpha=0.25,
        linewidth=0,
        # hatch="//////",
        label="COVID safe summer",
    )

    LAST_DATE = np.datetime64('2020-11-05T00', 'h')

    plt.fill_between(
        dates[1:][dates[1:] <= LAST_DATE] + 24,
        R[dates[1:] <= LAST_DATE],
        label=R"$R_\mathrm{eff}$",
        step='pre',
        color='C0',
    )

    plt.fill_between(
        dates[1:][dates[1:] <= LAST_DATE] + 24,
        R_lower[dates[1:] <= LAST_DATE],
        R_upper[dates[1:] <= LAST_DATE],
        label=R"$R_\mathrm{eff}$ uncertainty",
        color='cyan',
        edgecolor='blue',
        alpha=0.2,
        step='pre',
        zorder=2,
        # linewidth=0,
        hatch="////",
    )

    # # Reff values on given dates according to Dan Andrews infographic posted on facebook
    # # https://www.facebook.com/DanielAndrewsMP/photos/a.149185875145957/3350150198382826
    # gov_dates, gov_Reff = zip(
    #     *[
    #         ('2020-06-22', 1.72),
    #         ('2020-06-29', 1.61),
    #         ('2020-07-06', 1.33),
    #         ('2020-07-13', 1.26),
    #         ('2020-07-20', 1.17),
    #         ('2020-07-27', 0.97),
    #         ('2020-08-03', 0.86),
    #     ]
    # )
    # gov_dates = np.array([np.datetime64(d, 'h') for d in gov_dates])
    # plt.plot(gov_dates, gov_Reff, 'ro')

    plt.axhline(1.0, color='k', linewidth=1)
    plt.axis(
        xmin=START_PLOT, xmax=END_PLOT, ymin=0, ymax=3
    )
    plt.grid(True, linestyle=":", color='k', alpha=0.5)

    handles, labels = plt.gca().get_legend_handles_labels()

    plt.ylabel(R"$R_\mathrm{eff}$")

    u_R_latest = (R_upper[-1] - R_lower[-1]) / 2

    plt.title(
        "$R_\\mathrm{eff}$ in Victoria with Melbourne restriction levels and daily cases"
        + (
            "\n"
            + fR"Latest estimate: $R_\mathrm{{eff}}={R[-1]:.02f} \pm {u_R_latest:.02f}$"
            if ANIMATE
            else ""
        )
    )

    plt.gca().yaxis.set_major_locator(mticker.MultipleLocator(0.25))
    ax2 = plt.twinx()
    plt.step(all_dates + 24, all_new + 0.02, color='purple', alpha=0.25)
    plt.step(dates + 24, new + 0.02, color='purple', label='Daily cases')
    plt.semilogy(
        dates + 12, new_smoothed, color='magenta', label='Daily cases (smoothed)'
    )

    plt.fill_between(
        dates + 12,
        new_smoothed_lower,
        new_smoothed_upper,
        color='magenta',
        alpha=0.3,
        linewidth=0,
        zorder=10,
        label='Smoothing/trend uncertainty' if ANIMATE else 'Smoothing uncertainty',
    )
    if ANIMATE:
        plt.plot(
            dates[-1] + 12 + 24 * t_projection.astype('timedelta64[h]'),
            new_projection,
            color='magenta',
            linestyle='--',
            label='Daily cases (trend)',
        )
        plt.fill_between(
            dates[-1] + 12 + 24 * t_projection.astype('timedelta64[h]'),
            new_projection_lower,
            new_projection_upper,
            color='magenta',
            alpha=0.3,
            linewidth=0,
        )
    if ANIMATE:
        plt.axvline(
            dates[-1] + 24,
            linestyle='--',
            color='k',
            label=f'Today ({dates[-1].tolist().strftime("%b %d")})',
        )
    plt.axis(ymin=1, ymax=1000)
    plt.ylabel("Daily confirmed cases")
    plt.tight_layout()

    handles2, labels2 = plt.gca().get_legend_handles_labels()

    handles += handles2
    labels += labels2

    if ANIMATE:
        order = [8, 9, 10, 11, 12, 14, 13, 7, 0, 1, 3, 6, 2, 4, 5]
    else:
        order = [8, 9, 10, 11, 12, 7, 0, 1, 3, 6, 2, 4, 5]
    plt.legend(
        [handles[idx] for idx in order],
        [labels[idx] for idx in order],
        loc='upper right',
        ncol=2,
    )

    plt.gca().yaxis.set_major_formatter(mticker.ScalarFormatter())
    plt.gca().yaxis.set_minor_formatter(mticker.ScalarFormatter())
    plt.gca().tick_params(axis='y', which='minor', labelsize='x-small')
    plt.setp(plt.gca().get_yminorticklabels()[1::2], visible=False)
    plt.gca().xaxis.set_major_locator(mdates.DayLocator([1, 15]))

    fig2 = plt.figure(figsize=(10.8, 7))

    cases_and_projection = np.concatenate((new, new_projection[1:]))
    cases_and_projection_upper = np.concatenate((new, new_projection_upper[1:]))
    cases_and_projection_lower = np.concatenate((new, new_projection_lower[1:]))
    average_cases = fourteen_day_average(cases_and_projection)
    average_projection_upper = fourteen_day_average(cases_and_projection_upper)
    average_projection_lower = fourteen_day_average(cases_and_projection_lower)

    plt.step(
        unknowns_last_14d_dates[unknowns_last_14d_dates <= dates[-1]] + 24,
        unknowns_last_14d[unknowns_last_14d_dates <= dates[-1]],
        color='blue',
        label='14d total mystery cases* (DHHS)',
        alpha=1.0,
        zorder=5,
    )
    text = plt.figtext(
        0.575 if ANIMATE else 0.585,
        0.70 if ANIMATE else 0.79,
        "* 14d mystery cases must be below 5 to move to third step",
        fontsize='x-small',
    )
    text.set_bbox(dict(facecolor='white', alpha=0.8, linewidth=0))


    plt.step(
        all_dates + 24,
        fourteen_day_average(all_new),
        color='grey',
        alpha=0.25
    )
    plt.step(
        dates + 24,
        fourteen_day_average(new),
        color='grey',
        label='14d average daily cases',
    )

    in_range = dates[-1] + 24 * t_projection.astype('timedelta64[h]') < LAST_DATE

    if ANIMATE:
        plt.plot(
            (dates[-1] + 12 + 24 * t_projection.astype('timedelta64[h]'))[in_range],
            average_cases[-len(t_projection) :][in_range],
            color='grey',
            linestyle='--',
            label='14d average (trend)',
        )

        plt.fill_between(
            (dates[-1] + 12 + 24 * t_projection.astype('timedelta64[h]'))[in_range],
            average_projection_lower[-len(t_projection) :][in_range],
            average_projection_upper[-len(t_projection) :][in_range],
            color='grey',
            alpha=0.5,
            linewidth=0,
            label='Trend uncertainty',
        )

    if ANIMATE:
        plt.axvline(
            dates[-1] + 24,
            linestyle='--',
            color='k',
            label=f'Today ({dates[-1].tolist().strftime("%b %d")})',
        )
    plt.yscale('log')
    plt.axis(xmin=np.datetime64('2020-07-01', 'h'), xmax=END_PLOT, ymin=.05, ymax=1000)
    plt.grid(True, linestyle=":", color='k', alpha=0.5)
    plt.grid(True, linestyle=":", color='k', alpha=0.25, which='minor')
    plt.ylabel("Cases")

    STEP_ONE = np.datetime64('2020-09-14')
    plt.fill_betweenx(
        [0, 1000],
        [FIRST_STEP, FIRST_STEP],
        [SECOND_STEP, SECOND_STEP],
        color="red",
        alpha=0.5,
        linewidth=0,
        label="First step"
    )

    plt.fill_betweenx(
        [0, 1000],
        [SECOND_STEP, SECOND_STEP],
        [STEP_TWO_POINT_FIVE, STEP_TWO_POINT_FIVE],
        color="orange",
        alpha=0.5,
        linewidth=0,
        label="Second step"
    )

    plt.fill_betweenx(
        [0, 1000],
        [THIRD_STEP, THIRD_STEP],
        [STEP_TWO_POINT_FIVE, STEP_TWO_POINT_FIVE],
        color=ORANGEYELLOW,
        alpha=0.5,
        linewidth=0,
        label="Step 2.5"
    )

    plt.fill_betweenx(
        [0, 1000],
        [THIRD_STEP, THIRD_STEP],
        [LAST_STEP, LAST_STEP],
        color="yellow",
        alpha=0.5,
        linewidth=0,
        label="Third step"
    )

    plt.fill_betweenx(
        [-10, 1000],
        [LAST_STEP, LAST_STEP],
        [COVID_SAFE_SUMMER, COVID_SAFE_SUMMER],
        color="green",
        alpha=0.5,
        linewidth=0,
        label="Last step"
    )

    plt.fill_betweenx(
        [-10, 1000],
        [COVID_SAFE_SUMMER, COVID_SAFE_SUMMER],
        [END_PLOT, END_PLOT],
        color="green",
        # edgecolor="green",
        alpha=0.25,
        linewidth=0,
        # hatch="//////",
        label="COVID safe summer",
    )

    plt.step(
        [FIRST_STEP, SECOND_STEP, THIRD_STEP, LAST_STEP, COVID_SAFE_SUMMER],
        [2000, 50, 5, 1 / 14, 0],
        where='post',
        color='k',
        linewidth=2,
        label='Required target',
    )

    handles, labels = plt.gca().get_legend_handles_labels()

    if ANIMATE:
        order = [1, 2, 5, 4, 0, 3, 6, 7, 8, 9, 10, 11]
    else:
        order = [1, 2, 0, 3, 4, 5, 6, 7]

    plt.legend(
        [handles[idx] for idx in order],
        [labels[idx] for idx in order],
        loc='upper right',
        ncol=2,
    )

    mysteries = unknowns_last_14d[unknowns_last_14d_dates <= dates[-1]]
    if len(mysteries):
        mysteries_str = f" Fortnightly mystery cases: {mysteries[-1]}"
    else:
        mysteries_str = ""

    plt.title(
        "VIC 14 day average with Melbourne reopening targets"
        + (
            f"\nCurrent average: {average_cases[len(dates) - 1]:.1f} cases per day."
            if ANIMATE
            else ""
        )
        + (mysteries_str if ANIMATE else "")
    )

    def format(value, pos):
        if value >= 1:
            return str(int(round(value)))
        elif value >= 0.1:
            return f"{round(value, 1):.1f}"
        else:
            return f"{round(value, 2):.2f}"

    plt.tight_layout()
    plt.gca().yaxis.set_major_formatter(mticker.FuncFormatter(format))
    plt.gca().yaxis.set_minor_formatter(mticker.FuncFormatter(format))

    plt.gca().tick_params(axis='y', which='minor', labelsize='x-small')
    plt.setp(plt.gca().get_yminorticklabels()[1::2], visible=False)
    plt.gca().xaxis.set_major_locator(mdates.DayLocator([1, 15]))

    if ANIMATE:
        print(j)
        fig1.savefig(Path('VIC-animated', f'reff_{j:04d}.png'), dpi=150)
        fig2.savefig(Path('VIC-animated', f'reopening_{j:04d}.png'), dpi=150)
        plt.close(fig1)
        plt.close(fig2)
    else:
        fig1.savefig('COVID_VIC.svg')
        fig2.savefig('COVID_VIC_reopening.svg')
        plt.show()

        # Update the date in the HTML
        html_file = 'COVID_VIC.html'
        html_lines = Path(html_file).read_text().splitlines()
        now = datetime.now(timezone('Australia/Melbourne')).strftime('%Y-%m-%d-%H:%M')
        for i, line in enumerate(html_lines):
            if 'Last updated' in line:
                html_lines[i] = f'    Last updated: {now} Melbourne time'
        Path(html_file).write_text('\n'.join(html_lines) + '\n')

# WEEK_CYCLE_START = 178

# deviation = (new - new_smoothed) / new_smoothed

# deviations = []
# errors = []
# for i in range(7):
#     dat = deviation[WEEK_CYCLE_START + i :: 7]
#     deviations.append(dat.mean())
#     errors.append(dat.std() / np.sqrt(len(dat)))

# plt.bar(list(range(7)), deviations)
# plt.errorbar(list(range(7)), deviations, errors, color='k', fmt='none')
# plt.grid(True)
# plt.show()

# if ANIMATE:
#     GIF_START = 154
#     DELAY = 3000
#     for name in ['reff', 'reopening']:
#         subprocess.check_call(
#             ['convert', '-delay', '25']
#             + [f'VIC-animated/{name}_{j:04d}.png' for j in range(GIF_START, len(dates))]
#             + [
#                 '-delay',
#                 '500',
#                 f'VIC-animated/{name}_{len(dates):04d}.png',
#                 f'{name}.gif',
#             ],
#         )
