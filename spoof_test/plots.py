"""Статические отчёты по одному полёту из выровненного CSV PX4."""

from __future__ import annotations

from pathlib import Path


PLOT_NAMES = ("trajectory", "altitude", "diagnostics")


def render_flight_plots(csv_path: Path, destination: Path, stem: str) -> list[Path]:
    """Создаёт три PNG; отсутствующие потоки помечаются на рисунке явно."""
    import numpy as np
    import pandas as pd
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    data = pd.read_csv(csv_path, low_memory=False)
    if "time_s" not in data or data.empty:
        raise ValueError(f"нет временных меток в {csv_path}")
    time = pd.to_numeric(data["time_s"], errors="coerce")

    def values(column):
        return pd.to_numeric(data[column], errors="coerce") if column in data else None

    def missing(ax, text="No data recorded"):
        ax.text(0.5, 0.5, text, ha="center", va="center", transform=ax.transAxes)

    def lines(ax, columns, ylabel):
        plotted = False
        for column, label in columns:
            series = values(column)
            if series is not None and series.notna().any():
                ax.plot(time, series, label=label, linewidth=1.2)
                plotted = True
        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.3)
        if plotted:
            ax.legend(loc="best", fontsize="small")
        else:
            missing(ax)

    outputs = []

    fig = Figure(figsize=(10, 7), dpi=120, layout="constrained")
    ax = fig.subplots()
    # Все кривые в одной локальной системе координат с общей точкой отсчёта.
    tracks = (
        ("vehicle_global_position_groundtruth", "Truth"),
        ("vehicle_global_position", "PX4 estimate"),
        ("vehicle_gps_position", "GNSS"),
    )
    coordinates = []
    for prefix, label in tracks:
        lat_name = f"{prefix}.latitude_deg" if prefix == "vehicle_gps_position" else f"{prefix}.lat"
        lon_name = f"{prefix}.longitude_deg" if prefix == "vehicle_gps_position" else f"{prefix}.lon"
        lat, lon = values(lat_name), values(lon_name)
        if lat is not None and lon is not None:
            valid = lat.between(-90, 90) & lon.between(-180, 180) & lat.notna() & lon.notna()
            if valid.any():
                coordinates.append((lat[valid], lon[valid], label))
    if coordinates:
        origin_lat = float(coordinates[0][0].dropna().iloc[0])
        origin_lon = float(coordinates[0][1].dropna().iloc[0])
        for lat, lon, label in coordinates:
            east = (lon - origin_lon) * (111320 * np.cos(np.radians(origin_lat)))
            north = (lat - origin_lat) * 111195
            ax.plot(east, north, label=label, linewidth=1.2)
            ax.plot(east.dropna().iloc[0], north.dropna().iloc[0], marker="o", markersize=4)
        ax.legend()
    else:
        missing(ax, "No position recorded")
    ax.set(title=f"{stem}: horizontal track", xlabel="East from origin (m)", ylabel="North from origin (m)")
    ax.grid(alpha=0.3)
    ax.set_aspect("equal", adjustable="datalim")
    path = destination / f"{stem}_trajectory.png"
    FigureCanvasAgg(fig).print_png(path)
    outputs.append(path)

    fig = Figure(figsize=(11, 7), dpi=120, layout="constrained")
    ax = fig.subplots()
    lines(ax, (
        ("vehicle_global_position_groundtruth.alt", "Truth"),
        ("vehicle_global_position.alt", "PX4 estimate"),
        ("vehicle_gps_position.altitude_msl_m", "GNSS"),
    ), "Altitude AMSL (m)")
    ax.set(title=f"{stem}: altitude", xlabel="Time since log start (s)")
    path = destination / f"{stem}_altitude.png"
    FigureCanvasAgg(fig).print_png(path)
    outputs.append(path)

    fig = Figure(figsize=(11, 10), dpi=120, layout="constrained")
    axes = fig.subplots(3, 1, sharex=True)
    lines(axes[0], (
        ("gps_vs_truth.horizontal_error_m", "GNSS vs truth"),
        ("estimate_vs_truth.horizontal_error_m", "PX4 vs truth"),
        ("estimator_status.pos_horiz_accuracy", "Estimated horizontal accuracy"),
    ), "Horizontal error / accuracy (m)")
    lines(axes[1], (
        ("vehicle_gps_position.satellites_used", "Satellites"),
        ("vehicle_gps_position.fix_type", "GNSS fix type"),
        ("vehicle_gps_position.spoofing_state", "Spoofing state"),
    ), "GNSS indicators (different scales)")
    lines(axes[2], (
        ("vehicle_status.arming_state", "Arming state"),
        ("vehicle_status.nav_state", "Navigation state"),
        ("vehicle_status.failsafe", "Failsafe (0/1)"),
        ("mission_result.failure", "Mission failure (0/1)"),
    ), "PX4 state codes / flags")
    axes[2].set_xlabel("Time since log start (s)")
    fig.suptitle(f"{stem}: flight diagnostics (state values are PX4 codes)")
    path = destination / f"{stem}_diagnostics.png"
    FigureCanvasAgg(fig).print_png(path)
    outputs.append(path)
    return outputs
