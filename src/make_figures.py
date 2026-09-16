"""Render the four report figures, one per Power BI page.

Palette is two colours, blue and red, used as a diverging pair: blue for the
on-time / normal pole, red for the late / below-average pole. Validated
all-pairs on the light surface (CVD dE 21.6, normal-vision dE 32.3), so the
polarity survives colourblind viewing. Every chart also direct-labels its
values, so colour never carries meaning on its own.

No chart here uses two y-axes. Where two measures of different scale belong
together they are stacked as small multiples instead, which keeps both readable
and stops the reader inferring a crossover that is an artefact of the scaling.
"""

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TABLES = ROOT / "reports" / "tables"
FIGS = ROOT / "reports" / "figures"

BLUE = "#2a78d6"
RED = "#e34948"
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"


def setup():
    mpl.rcParams.update({
        "font.family": ["Segoe UI", "Arial", "DejaVu Sans", "sans-serif"],
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "axes.edgecolor": AXIS,
        "axes.labelcolor": INK_2,
        "text.color": INK,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "axes.titlesize": 12,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "figure.dpi": 160,
        "savefig.dpi": 160,
        "savefig.bbox": "tight",
    })


def titled(ax, title, subtitle=None):
    ax.set_title(title, loc="left", color=INK, fontweight="bold",
                 pad=26 if subtitle else 8)
    if subtitle:
        ax.text(0, 1.02, subtitle, transform=ax.transAxes, color=INK_2, fontsize=9.5,
                va="bottom")


def tidy(ax, axis="y"):
    ax.grid(axis=axis, zorder=0)
    ax.set_axisbelow(True)
    for side in ("left", "bottom"):
        ax.spines[side].set_linewidth(0.8)


# 1 -------------------------------------------------------------------------
def fig_monthly_sales():
    """Revenue and AOV as small multiples, never as a dual axis."""
    m = pd.read_csv(TABLES / "monthly_sales.csv", parse_dates=["order_month"])
    # 2018-09 holds a single order: the export stops on 2018-09-03. Plotting it
    # draws a cliff that is a collection artefact, not a business event.
    full = m[m["orders"] > 50]
    dropped = len(m) - len(full)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True,
                                   gridspec_kw={"hspace": 0.32})

    ax1.plot(full["order_month"], full["revenue"] / 1000, color=BLUE, linewidth=2,
             marker="o", markersize=4.5, markerfacecolor=BLUE,
             markeredgecolor=SURFACE, markeredgewidth=1.2, zorder=3)
    ax1.set_ylabel("R$ thousands")
    ax1.set_ylim(0, None)
    titled(ax1, "Monthly revenue",
           "Item value (price + freight) on non-cancelled orders")
    tidy(ax1)

    peak = full.loc[full["revenue"].idxmax()]
    ax1.annotate(f"peak R${peak['revenue'] / 1000:,.0f}k",
                 xy=(peak["order_month"], peak["revenue"] / 1000),
                 xytext=(0, 14), textcoords="offset points",
                 ha="center", color=INK_2, fontsize=9)

    ax2.plot(full["order_month"], full["aov"], color=BLUE, linewidth=2,
             marker="o", markersize=4.5, markerfacecolor=BLUE,
             markeredgecolor=SURFACE, markeredgewidth=1.2, zorder=3)
    ax2.set_ylabel("R$ per order")
    ax2.set_ylim(0, max(full["aov"]) * 1.25)
    titled(ax2, "Average order value",
           "Revenue divided by distinct orders, not by item lines. Flat near R$160 throughout")
    tidy(ax2)

    overall = full["revenue"].sum() / full["orders"].sum()
    ax2.axhline(overall, color=MUTED, linewidth=1, linestyle=(0, (4, 3)), zorder=2)
    # Below the line and hard left: the series sits above 160 at that end, so the
    # label cannot land on top of it.
    ax2.text(full["order_month"].iloc[0], overall - 8,
             f"period average R${overall:,.0f}", color=INK_2, fontsize=9, va="top")

    skipped = ", ".join(f"{d:%Y-%m}" for d in m.loc[m["orders"] <= 50, "order_month"])
    fig.text(0.008, -0.02,
             f"Source: Olist public dataset, {full['order_month'].min():%b %Y} to "
             f"{full['order_month'].max():%b %Y}. Months with under 50 orders "
             f"excluded ({skipped}) - the platform had almost no volume before 2017 "
             f"and the export stops on 2018-09-03.",
             color=MUTED, fontsize=8.5)
    fig.savefig(FIGS / "01_monthly_sales.png")
    plt.close(fig)


# 2 -------------------------------------------------------------------------
def fig_delivery_vs_review():
    """The headline: review score against how the delivery promise was kept."""
    d = pd.read_csv(TABLES / "delivery_vs_review.csv")
    order = ["10+ days early", "3-10 days early", "0-3 days early",
             "1-3 days late", "3-10 days late", "10+ days late"]
    d = d.set_index("delivery_bucket").loc[order].reset_index()
    # Blue = arrived by the promised date, red = missed it. Polarity, not identity.
    colors = [BLUE if "early" in b else RED for b in d["delivery_bucket"]]

    fig, ax = plt.subplots(figsize=(10, 5.4))
    y = range(len(d))
    ax.barh(y, d["avg_review"], color=colors, height=0.62, zorder=3)
    ax.set_yticks(list(y), d["delivery_bucket"], fontsize=10)
    ax.invert_yaxis()
    # Axis runs to 5 but the view runs to 5.9, reserving a gutter for the order
    # counts so they cannot collide with the value label on the longest bar.
    ax.set_xlim(0, 5.9)
    ax.set_xticks([1, 2, 3, 4, 5])
    ax.set_xlabel("Average review score (1-5)")
    tidy(ax, axis="x")

    for i, row in d.iterrows():
        ax.text(row["avg_review"] + 0.08, i, f"{row['avg_review']:.2f}",
                va="center", color=INK, fontsize=10, fontweight="bold")
        ax.text(5.88, i, f"{row['orders']:,} orders", va="center", ha="right",
                color=MUTED, fontsize=9)

    titled(ax, "A late delivery costs about two stars",
           "Delivered, non-cancelled orders grouped by days early or late "
           "against the promised date")
    fig.text(0.008, -0.03,
             "Association, not proof of cause: late orders may also differ in "
             "product type, distance and seller.",
             color=MUTED, fontsize=8.5)
    fig.savefig(FIGS / "02_delivery_vs_review.png")
    plt.close(fig)


# 3 -------------------------------------------------------------------------
def fig_category_performance():
    """Revenue and satisfaction side by side, sharing one category axis."""
    c = pd.read_csv(TABLES / "category_performance.csv").head(12)
    overall = 4.10

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 6), sharey=True,
                                   gridspec_kw={"wspace": 0.06, "width_ratios": [1.5, 1]})
    y = range(len(c))

    ax1.barh(y, c["revenue"] / 1000, color=BLUE, height=0.62, zorder=3)
    ax1.set_yticks(list(y), c["product_category"], fontsize=10)
    ax1.invert_yaxis()
    ax1.set_xlabel("Revenue, R$ thousands")
    tidy(ax1, axis="x")
    for i, v in enumerate(c["revenue"] / 1000):
        ax1.text(v + 18, i, f"{v:,.0f}", va="center", color=INK_2, fontsize=9)
    titled(ax1, "Top 12 categories by revenue", "Item grain: price + freight per line")

    # Red marks a category below the 4.10 average. Colour repeats what the
    # position already shows, so the encoding is redundant rather than load-bearing.
    dot_colors = [RED if v < overall else BLUE for v in c["avg_review"]]
    ax2.axvline(overall, color=MUTED, linewidth=1, linestyle=(0, (4, 3)), zorder=2)
    ax2.scatter(c["avg_review"], list(y), s=90, color=dot_colors,
                edgecolor=SURFACE, linewidth=1.5, zorder=3)
    ax2.set_xlim(3.2, 4.6)
    ax2.set_xlabel("Average review score")
    tidy(ax2, axis="x")
    for i, v in enumerate(c["avg_review"]):
        ax2.text(v, i - 0.42, f"{v:.2f}", ha="center", color=INK_2, fontsize=9)
    ax2.text(overall + 0.015, len(c) - 0.3, f"all categories {overall:.2f}",
             color=MUTED, fontsize=9)
    titled(ax2, "Satisfaction", "Red = below the overall average")

    fig.text(0.008, -0.02,
             "Revenue rank and review score are close to independent: the largest "
             "categories are neither the best nor the worst rated.",
             color=MUTED, fontsize=8.5)
    fig.savefig(FIGS / "03_category_performance.png")
    plt.close(fig)


# 4 -------------------------------------------------------------------------
def fig_state_delivery():
    """Two explanations for the same y-axis, so the weaker one is visibly weaker.

    Absolute delivery time looks like the obvious driver of satisfaction. Across
    states it is not: the promise being kept explains twice as much. Putting both
    scatters on a shared y-axis makes the comparison structural rather than a
    claim in a caption.
    """
    s = pd.read_csv(TABLES / "state_performance.csv")
    sizes = 24 + (s["revenue"] / s["revenue"].max()) * 850
    colors = [RED if r < 4.0 else BLUE for r in s["avg_review"]]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 5.8), sharey=True,
                                   gridspec_kw={"wspace": 0.07})

    panels = [
        (ax1, "avg_delivery_days", "Average delivery time, days",
         "Against how long it takes"),
        (ax2, "late_rate", "Share of orders delivered after the promised date",
         "Against how often the promise breaks"),
    ]
    for ax, xcol, xlabel, title in panels:
        ax.scatter(s[xcol], s["avg_review"], s=sizes, color=colors,
                   alpha=0.82, edgecolor=SURFACE, linewidth=1.6, zorder=3)
        ax.axhline(4.0, color=MUTED, linewidth=1, linestyle=(0, (4, 3)), zorder=2)
        ax.set_xlabel(xlabel)
        tidy(ax, axis="both")
        r = s[xcol].corr(s["avg_review"])
        titled(ax, title, f"correlation with review score  r = {r:+.2f}")
    ax1.set_ylabel("Average review score")
    ax2.xaxis.set_major_formatter(lambda v, _: f"{v:.0%}")

    # AM and AL are the argument in two dots: similar delivery times, opposite
    # outcomes, because their promises differ by 13 days.
    for ax, xcol in ((ax1, "avg_delivery_days"), (ax2, "late_rate")):
        for code, dy in (("AM", 15), ("AL", 15), ("SP", 15), ("RJ", -26)):
            row = s[s["customer_state"] == code].iloc[0]
            ax.annotate(code, (row[xcol], row["avg_review"]),
                        xytext=(0, dy), textcoords="offset points", ha="center",
                        color=INK, fontsize=9.5, fontweight="bold")

    fig.suptitle("Customers punish the broken promise, not the wait",
                 x=0.09, y=1.10, ha="left", color=INK, fontsize=14,
                 fontweight="bold")
    fig.text(0.09, 1.045,
             "One bubble per state, sized by revenue. Red = average review below 4.0. "
             "Same 27 states and the same y-axis in both panels.",
             color=INK_2, fontsize=9.5)
    fig.text(0.008, -0.04,
             "AM averages 26.4 days but promises 45, so only 2.8% of its orders are "
             "late and it rates 4.23. AL averages 24.5 days against a 32.6-day promise, "
             "runs 21.4% late, and rates 3.83.",
             color=MUTED, fontsize=8.5)
    fig.savefig(FIGS / "04_state_delivery.png")
    plt.close(fig)


def main():
    FIGS.mkdir(parents=True, exist_ok=True)
    setup()
    for fn in (fig_monthly_sales, fig_delivery_vs_review,
               fig_category_performance, fig_state_delivery):
        fn()
        print(f"rendered {fn.__name__}")


if __name__ == "__main__":
    main()
