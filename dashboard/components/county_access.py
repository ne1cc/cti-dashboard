"""County map of straight-line access to open trial sites (Issue #73).

Intent: turn ``mart_trial_access_by_geo`` (Issue #72) rows for one or two conditions into a
county choropleth, the always-visible denominator / coverage / data-date / interpretation
text, a rural "no site within 100 miles" table and a CSV with a methodology header. The
logic is plain pandas so it is testable without Streamlit; ``render_county_access`` is the
only function that draws.

Contract:
    - Input rows are the mart's, unchanged (docs/access_methodology.md). Nothing is
      recomputed from sites, so every figure here is the mart's figure.
    - A county the data cannot speak for is "insufficient data" (grey, excluded from every
      total), never zero: population under ``MIN_COUNTY_POPULATION``, or a condition whose
      open sites have no usable coordinates (every ring flag would be a false "no").
    - ``nearest_site_miles`` is null beyond 100 miles. That is "no open site within 100
      miles", shown as such and counted as farther than 100 in the median.

Limits:
    - Straight-line miles from the county centroid, not travel time or residents' own
      distance. The county seed covers only the states in ``county_access_context``.
    - Plotly hover tooltips cannot take keyboard focus; the same facts are in the table and
      the CSV below the map, which is the keyboard path.
    - shortcut: ``MIN_COUNTY_POPULATION`` is a judgement call about ACS 5-year noise, not a
      calibrated threshold; revisit it once the seed covers small counties.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from components import data
from components.formatting import format_snapshot_date
from src.presets import Preset

MIN_COUNTY_POPULATION = 5_000
DISTANCES = (25, 50, 100)
RURAL_GROUPS = ("small_town", "rural")
METRIC_POPULATION = "Population within N miles"
METRIC_DISTANCE = "Distance to nearest open site"
COLOR_SCALE = "Viridis"  # perceptually uniform and colour-blind safe
INSUFFICIENT_COLOR = "#bdbdbd"
GEOJSON = Path(__file__).resolve().parents[1] / "static" / "us_counties.geojson"
METHODOLOGY = (
    "Straight-line (great-circle) miles from the county centroid to the nearest open site. "
    "Open site = site and trial status RECRUITING or NOT_YET_RECRUITING. Not travel time. "
    "Source: mart_trial_access_by_geo; method and limits: docs/access_methodology.md."
)
MEANING = (
    "**What this means:** a county marked within N miles has its centroid within N "
    "straight-line miles of at least one registry-listed open site for the condition. "
    "**What it does not mean:** that residents can reach or enrol in a trial, that travel "
    "takes N miles of driving, that every resident of the county lives near the centroid, "
    "that a trial is a good fit, or that anyone needs it. Grey counties lack enough data "
    "to say; they are not zero."
)


@dataclass(frozen=True)
class Summary:
    """Denominator and headline figures for the counties that can be assessed."""

    counties: int
    insufficient: int
    population: int
    covered_population: int
    median_miles: float | None  # None: the median county resident is beyond 100 miles

    @property
    def share(self) -> float | None:
        return self.covered_population / self.population if self.population else None


def classify(frame: pd.DataFrame, miles: int) -> pd.DataFrame:
    """Add ``reason`` (insufficient-data cause or ``""``), ``in_range`` and ``residents_within``."""
    out = frame.copy()
    small = out["population"].isna() | (out["population"] < MIN_COUNTY_POPULATION)
    no_sites = out["coverage_confidence"].eq("none")
    out["reason"] = np.select(
        [small, no_sites],
        [
            f"population under {MIN_COUNTY_POPULATION:,}",
            "no open site has usable coordinates",
        ],
        default="",
    )
    out["in_range"] = out[f"within_{miles}mi"].astype(bool) & out["reason"].eq("")
    out["residents_within"] = np.where(out["in_range"], out["population"], 0)
    return out


def weighted_median(values: pd.Series, weights: pd.Series) -> float | None:
    """Population-weighted median; null values (beyond 100 miles) sort last. None if beyond."""
    ordered = pd.DataFrame({"v": values.fillna(np.inf), "w": weights}).sort_values("v")
    if ordered.empty:
        return None
    middle = ordered["w"].cumsum() >= ordered["w"].sum() / 2
    value = float(ordered.loc[middle, "v"].iloc[0])
    return None if np.isinf(value) else value


def summarize(classified: pd.DataFrame) -> Summary:
    """Totals over assessable counties only; insufficient-data counties are counted apart."""
    ok = classified[classified["reason"] == ""]
    return Summary(
        counties=len(ok),
        insufficient=len(classified) - len(ok),
        population=int(ok["population"].sum()),
        covered_population=int(ok["residents_within"].sum()),
        median_miles=weighted_median(ok["nearest_site_miles"], ok["population"])
        if len(ok)
        else None,
    )


def apply_filters(frame: pd.DataFrame, ruca: list[str], svi: list[str]) -> pd.DataFrame:
    """Keep counties in the chosen RUCA groups and SVI quartiles (``unranked`` = no SVI rank)."""
    quartile = frame["svi_quartile"].map(lambda q: "unranked" if pd.isna(q) else f"Q{int(q)}")
    return frame[frame["ruca_group"].isin(ruca) & quartile.isin(svi)]


def rural_gaps(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Rural / small-town counties with no open site within 100 miles for any condition.

    One column per condition: ``no site within 100 mi``, ``site within 100 mi`` or
    ``insufficient data``. Counties flagged for no condition are dropped.
    """
    if not frames:
        return pd.DataFrame()
    describe = ["county_fips", "county_name", "state_code", "ruca_group", "population"]
    statuses: dict[str, pd.DataFrame] = {}
    for label, frame in frames.items():
        c = classify(frame[frame["ruca_group"].isin(RURAL_GROUPS)], 100)
        flag = np.select(
            [c["reason"] != "", ~c["in_range"]],
            ["insufficient data", "no site within 100 mi"],
            default="site within 100 mi",
        )
        statuses[label] = c[describe].assign(**{label: flag})
    merged = pd.concat(statuses.values())[describe].drop_duplicates("county_fips")
    for label, part in statuses.items():
        merged = merged.merge(part[["county_fips", label]], on="county_fips", how="left")
    labels = list(frames)
    hit = (merged[labels] == "no site within 100 mi").any(axis=1)
    return merged[hit].sort_values(["state_code", "county_name"]).reset_index(drop=True)


def _hover(row: pd.Series, miles: int) -> str:
    svi = "unranked" if pd.isna(row["svi_quartile"]) else f"Q{int(row['svi_quartile'])}"
    if row["reason"]:
        status = f"Insufficient data: {row['reason']}"
    else:
        near = (
            f"{row['nearest_site_miles']:.1f} mi"
            if pd.notna(row["nearest_site_miles"])
            else "none within 100 mi"
        )
        status = (
            f"Nearest open site: {near}<br>Within {miles} mi: {'yes' if row['in_range'] else 'no'}"
        )
    return (
        f"{row['county_name']}, {row['state_code']}<br>Residents: {row['population']:,.0f}"
        f"<br>RUCA: {row['ruca_group']} · SVI: {svi}<br>{status}"
    )


@st.cache_data
def _geojson() -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(GEOJSON.read_text(encoding="utf-8")))


def county_geojson(fips: list[str]) -> dict[str, Any]:
    """Only the shown counties' shapes: the full file would ship ~2 MB with every figure."""
    keep = set(fips)
    return {
        "type": "FeatureCollection",
        "features": [f for f in _geojson()["features"] if f["id"] in keep],
    }


def build_figure(classified: pd.DataFrame, miles: int, metric: str) -> go.Figure:
    """Choropleth of the chosen metric; insufficient-data counties are a separate grey trace."""
    shown = classified[classified["reason"] == ""]
    gaps = classified[classified["reason"] != ""]
    by_pop = metric == METRIC_POPULATION
    z = shown["residents_within"] if by_pop else shown["nearest_site_miles"].fillna(100.0)
    title = f"Residents within {miles} mi" if by_pop else "Miles to nearest open site"
    geojson = county_geojson(classified["county_fips"].tolist())
    fig = go.Figure(
        go.Choropleth(
            geojson=geojson,
            locations=shown["county_fips"],
            z=z,
            zmin=0,
            zmax=None if by_pop else 100,
            colorscale=COLOR_SCALE,
            reversescale=not by_pop,  # nearer is brighter in both views
            text=[_hover(r, miles) for _, r in shown.iterrows()],
            hovertemplate="%{text}<extra></extra>",
            colorbar={"title": {"text": title}},
            name="Assessed counties",
            marker_line_width=0.5,
        )
    )
    fig.add_trace(
        go.Choropleth(
            geojson=geojson,
            locations=gaps["county_fips"],
            z=[0] * len(gaps),
            colorscale=[[0, INSUFFICIENT_COLOR], [1, INSUFFICIENT_COLOR]],
            showscale=False,
            showlegend=True,
            name="Insufficient data (not zero)",
            text=[_hover(r, miles) for _, r in gaps.iterrows()],
            hovertemplate="%{text}<extra></extra>",
            marker_line_width=0.5,
        )
    )
    fig.update_geos(fitbounds="locations", visible=False)
    fig.update_layout(margin={"l": 0, "r": 0, "t": 0, "b": 0}, legend={"orientation": "h"})
    return fig


def coverage_text(frame: pd.DataFrame) -> str:
    """Site-coordinate coverage of the condition (repeated on every mart row of a node)."""
    r = frame.iloc[0]
    return (
        f"Site coverage: {r['coverage_confidence']} confidence · {int(r['sites_total'])} open "
        f"sites, {int(r['sites_exact'])} on registry coordinates, "
        f"{int(r['sites_zip_approx'])} placed by ZIP, {int(r['sites_city_approx'])} by city, "
        f"{int(r['sites_excluded'])} excluded (no coordinates)."
    )


def summary_text(summary: Summary, miles: int, states: list[str], vintage: str | None) -> str:
    """Denominator line: who is counted, who is not."""
    share = "n/a" if summary.share is None else f"{summary.share:.0%}"
    median = (
        "n/a"
        if not summary.counties
        else "over 100 mi"
        if summary.median_miles is None
        else f"{summary.median_miles:.0f} mi"
    )
    return (
        f"Denominator: {summary.population:,} residents in {summary.counties} assessed "
        f"counties ({', '.join(states) or 'no states'}; "
        f"{vintage or 'population vintage unknown'}). "
        f"Within {miles} mi: {summary.covered_population:,} ({share}). Population-weighted "
        f"median distance to nearest open site: {median}. "
        f"Excluded as insufficient data: {summary.insufficient} counties."
    )


def methodology_header(
    condition: str, miles: int, metric: str, data_date: str, filters: str, summary: Summary
) -> list[str]:
    """Comment lines that travel with the CSV."""
    return [
        "County access to open trial sites (Issue #73)",
        f"Condition: {condition}",
        f"Metric shown: {metric}; distance: {miles} mi; data date: {data_date}",
        f"Filters: {filters}",
        f"Denominator: {summary.population:,} residents in {summary.counties} assessed "
        f"counties; {summary.insufficient} excluded as insufficient data "
        f"(population under {MIN_COUNTY_POPULATION:,} or no usable site coordinates)",
        f"Method: {METHODOLOGY}",
        "Blank nearest_site_miles with within_100mi=false means no open site within 100 miles.",
    ]


def to_csv(classified: pd.DataFrame, header: list[str]) -> str:
    """CSV text: ``#`` methodology lines, then one row per county."""
    cols = [
        "county_fips", "county_name", "state_code", "population", "ruca_group", "svi_quartile",
        "nearest_site_miles", "within_25mi", "within_50mi", "within_100mi", "coverage_confidence",
        "reason",
    ]  # fmt: skip
    body = classified[cols].rename(columns={"reason": "insufficient_data_reason"})
    return "".join(f"# {line}\n" for line in header) + body.to_csv(index=False)


def render_county_access(preset: Preset) -> None:
    """Draw the county section: controls, map(s), always-visible context, table and CSV."""
    st.subheader("County access to open trial sites")
    st.info(MEANING)
    rows = data.county_access(preset)
    labels = sorted(rows["condition_label"].dropna().unique()) if not rows.empty else []
    if not labels:
        st.caption(
            "No county access rows for this preset yet. Rebuild the marts (`make pipeline`); "
            "no county is shown as zero. " + METHODOLOGY
        )
        return
    data_date = format_snapshot_date(data.county_access_data_date(preset))

    c1, c2, c3, c4 = st.columns(4)
    first = c1.selectbox("Condition", labels, key="county_condition")
    other = c2.selectbox("Compare with", ["(none)", *labels], key="county_compare")
    miles = c3.radio("Distance (miles)", DISTANCES, index=2, horizontal=True, key="county_miles")
    metric = c4.radio("Map shows", [METRIC_POPULATION, METRIC_DISTANCE], key="county_metric")
    f1, f2 = st.columns(2)
    rucas = sorted(rows["ruca_group"].dropna().unique())
    ruca = f1.multiselect("Rural-urban group (RUCA)", rucas, default=rucas, key="county_ruca")
    svis = ["Q1", "Q2", "Q3", "Q4", "unranked"]
    svi = f2.multiselect(
        "Social vulnerability (SVI quartile)", svis, default=svis, key="county_svi"
    )
    filters = f"RUCA {', '.join(ruca) or 'none'}; SVI {', '.join(svi) or 'none'}"

    chosen = [first] if other in ("(none)", first) else [first, other]
    scoped: dict[str, pd.DataFrame] = {}
    for label, col in zip(chosen, st.columns(len(chosen)), strict=True):
        frame = apply_filters(rows[rows["condition_label"] == label], ruca, svi)
        scoped[label] = frame
        with col:
            st.markdown(f"**{label}**")
            if frame.empty:
                st.info("No counties match the filters.")
                continue
            classified = classify(frame, int(miles))
            summary = summarize(classified)
            vintage = (
                frame["population_vintage"].dropna().iloc[0]
                if frame["population_vintage"].notna().any()
                else None
            )
            st.plotly_chart(
                build_figure(classified, int(miles), metric),
                width="stretch",
                key=f"county_map_{label}",
            )
            st.caption(
                f"Data date: {data_date} (latest capture). "
                + summary_text(
                    summary, int(miles), sorted(frame["state_code"].dropna().unique()), vintage
                )
            )
            st.caption(coverage_text(frame))
            st.dataframe(
                classified[
                    ["county_name", "state_code", "population", "nearest_site_miles", "reason"]
                ].rename(columns={"reason": "insufficient data"}),
                hide_index=True,
                width="content",
            )
            st.download_button(
                "Download CSV",
                to_csv(
                    classified,
                    methodology_header(label, int(miles), metric, data_date, filters, summary),
                ),
                file_name=f"county_access_{label.lower().replace(' ', '_')}_{miles}mi.csv",
                mime="text/csv",
                key=f"county_csv_{label}",
            )

    st.markdown("**Rural counties with no open site within 100 miles**")
    gaps = rural_gaps(scoped)
    if gaps.empty:
        st.caption("None among the counties shown (insufficient-data counties are not counted).")
    else:
        st.dataframe(gaps, hide_index=True, width="content")
    st.caption(METHODOLOGY)
