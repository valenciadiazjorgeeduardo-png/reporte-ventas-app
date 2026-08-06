from __future__ import annotations

from io import BytesIO

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from analytics_core import (
    CANONICAL_FIELDS,
    apply_dimension_filters,
    calculate_metrics,
    change_pct,
    comparison_window,
    detect_columns,
    prepare_data,
    to_excel_bytes,
)


st.set_page_config(
    page_title="Analítica de Ventas",
    page_icon="📊",
    layout="wide",
)

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.2rem; padding-bottom: 2rem;}
    [data-testid="stMetricValue"] {font-size: 1.65rem;}
    [data-testid="stMetricLabel"] {font-weight: 600;}
    .small-note {color: #5b6573; font-size: 0.88rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def read_excel_file(file_bytes: bytes, sheet_name: str) -> pd.DataFrame:
    return pd.read_excel(BytesIO(file_bytes), sheet_name=sheet_name)


@st.cache_data(show_spinner=False)
def get_sheet_names(file_bytes: bytes) -> list[str]:
    return pd.ExcelFile(BytesIO(file_bytes)).sheet_names


def money(value: float) -> str:
    return f"${value:,.0f}".replace(",", ".")


def number(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}".replace(",", ".")
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def delta_text(value):
    if value is None:
        return "Sin base comparable"
    return f"{value:+.2%}"


def monthly_series(df: pd.DataFrame) -> pd.DataFrame:
    result = (
        df.dropna(subset=["Fecha"])
        .assign(Periodo=lambda x: x["Fecha"].dt.to_period("M").dt.to_timestamp())
        .groupby("Periodo", as_index=False)["ValorVenta"]
        .sum()
        .sort_values("Periodo")
    )
    return result



def prepare_year_month_trend(
    df: pd.DataFrame,
    fill_historical_gaps: bool = False,
) -> pd.DataFrame:
    """Create a Jan-Dec comparison structure with one series per year."""
    source = df.dropna(subset=["Fecha"]).copy()
    columns = [
        "AñoComparacion",
        "MesNumeroComparacion",
        "MesComparacion",
        "ValorVenta",
        "VentaAcumulada",
        "TieneRegistros",
    ]
    if source.empty:
        return pd.DataFrame(columns=columns)

    source["AñoComparacion"] = source["Fecha"].dt.year.astype(str)
    source["MesNumeroComparacion"] = source["Fecha"].dt.month

    observed = (
        source.groupby(
            ["AñoComparacion", "MesNumeroComparacion"],
            as_index=False,
        )
        .agg(
            ValorVenta=("ValorVenta", "sum"),
            TieneRegistros=("ValorVenta", "size"),
        )
        .sort_values(["AñoComparacion", "MesNumeroComparacion"])
    )
    observed["TieneRegistros"] = observed["TieneRegistros"] > 0

    years = sorted(observed["AñoComparacion"].unique())
    full_grid = pd.MultiIndex.from_product(
        [years, list(range(1, 13))],
        names=["AñoComparacion", "MesNumeroComparacion"],
    ).to_frame(index=False)

    result = full_grid.merge(
        observed,
        on=["AñoComparacion", "MesNumeroComparacion"],
        how="left",
    )
    result["MesComparacion"] = result["MesNumeroComparacion"].map(MONTH_SHORT)
    result["TieneRegistros"] = result["TieneRegistros"].fillna(False)

    min_period = source["Fecha"].min().to_period("M")
    max_period = source["Fecha"].max().to_period("M")
    result["PeriodoComparacion"] = pd.PeriodIndex(
        result["AñoComparacion"].astype(str)
        + "-"
        + result["MesNumeroComparacion"].astype(str).str.zfill(2),
        freq="M",
    )
    result["DentroCobertura"] = (
        (result["PeriodoComparacion"] >= min_period)
        & (result["PeriodoComparacion"] <= max_period)
    )

    if fill_historical_gaps:
        historical_gap = (
            result["DentroCobertura"]
            & ~result["TieneRegistros"]
        )
        result.loc[historical_gap, "ValorVenta"] = 0.0

    result["VentaAcumulada"] = (
        result.groupby("AñoComparacion")["ValorVenta"]
        .transform(lambda series: series.fillna(0).cumsum())
    )

    # Months after the latest available month must remain blank, not zero.
    result.loc[~result["DentroCobertura"], "VentaAcumulada"] = pd.NA

    return result[columns + ["DentroCobertura"]]

def build_year_comparison_chart(
    df: pd.DataFrame,
    mode: str = "Ventas mensuales",
    title: str | None = None,
    missing_mode: str = "Mantener vacío",
):
    fill_historical_gaps = missing_mode == "Mostrar como cero"
    trend = prepare_year_month_trend(
        df,
        fill_historical_gaps=fill_historical_gaps,
    )
    if trend.empty or trend["ValorVenta"].notna().sum() == 0:
        return None

    cumulative = mode == "Ventas acumuladas"
    y_column = "VentaAcumulada" if cumulative else "ValorVenta"
    chart_title = title or (
        "Ventas acumuladas comparadas por año"
        if cumulative
        else "Comparativo mensual de ventas por año"
    )

    trend["EstadoDato"] = trend["TieneRegistros"].map(
        {True: "Con registros", False: "Sin registros"}
    )

    fig = px.line(
        trend,
        x="MesNumeroComparacion",
        y=y_column,
        color="AñoComparacion",
        markers=True,
        hover_name="MesComparacion",
        custom_data=["EstadoDato"],
        title=chart_title,
        labels={
            "AñoComparacion": "Año",
            "MesNumeroComparacion": "Mes",
            "ValorVenta": "Valor de ventas",
            "VentaAcumulada": "Ventas acumuladas",
        },
    )
    fig.update_traces(
        connectgaps=False,
        line={"width": 3},
        marker={"size": 8},
        hovertemplate=(
            "<b>%{hovertext}</b><br>"
            "Año: %{fullData.name}<br>"
            "Valor: $%{y:,.0f}<br>"
            "Estado: %{customdata[0]}"
            "<extra></extra>"
        ),
    )
    fig.update_xaxes(
        tickmode="array",
        tickvals=list(range(1, 13)),
        ticktext=[MONTH_SHORT[month] for month in range(1, 13)],
        title="Mes",
        range=[0.7, 12.3],
        fixedrange=False,
    )
    fig.update_yaxes(
        title="Valor de ventas acumulado" if cumulative else "Valor de ventas",
        tickprefix="$",
        tickformat="~s",
        separatethousands=True,
        rangemode="tozero",
    )
    fig.update_layout(
        height=470,
        hovermode="x unified",
        legend={
            "title_text": "",
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "center",
            "x": 0.5,
        },
        margin={"l": 20, "r": 20, "t": 85, "b": 35},
    )
    return fig


def build_budget_comparison(
    sales_df: pd.DataFrame,
    selected_years,
    selected_months,
    start_date,
    end_date,
    data_max_date,
) -> pd.DataFrame:
    actual = sales_df.dropna(subset=["Fecha"]).copy()
    actual_by_month = {}

    if not actual.empty:
        actual["AñoPresupuesto"] = actual["Fecha"].dt.year
        actual["MesPresupuesto"] = actual["Fecha"].dt.month
        actual_by_month = (
            actual.groupby(["AñoPresupuesto", "MesPresupuesto"])["ValorVenta"]
            .sum()
            .to_dict()
        )

    rows = []
    for year in selected_years:
        year = int(year)
        year_budget = CORPORATE_BUDGETS.get(year)
        if not year_budget:
            continue

        for month, budget in year_budget.items():
            if selected_months and month not in selected_months:
                continue

            period_start = pd.Timestamp(year=year, month=month, day=1)
            period_end = period_start + pd.offsets.MonthEnd(0)

            if period_end.date() < start_date or period_start.date() > end_date:
                continue

            is_future = period_start.date() > data_max_date
            is_partial = (
                year == data_max_date.year
                and month == data_max_date.month
                and data_max_date < period_end.date()
            )

            if is_future:
                actual_value = pd.NA
                difference = pd.NA
                compliance_pct = pd.NA
                status = "Pendiente"
            else:
                actual_value = float(actual_by_month.get((year, month), 0.0))
                difference = actual_value - budget
                compliance_pct = actual_value / budget * 100 if budget else pd.NA

                if is_partial:
                    status = f"Parcial al {data_max_date:%d/%m/%Y}"
                elif compliance_pct >= 100:
                    status = "Cumplido"
                elif compliance_pct >= 90:
                    status = "En riesgo"
                else:
                    status = "Bajo meta"

            rows.append(
                {
                    "Año": year,
                    "MesNumero": month,
                    "Mes": MONTH_NAMES[month],
                    "Presupuesto": float(budget),
                    "Ventas reales": actual_value,
                    "Diferencia": difference,
                    "Cumplimiento %": compliance_pct,
                    "Estado": status,
                    "Es futuro": is_future,
                }
            )

    return pd.DataFrame(rows)


def build_budget_chart(comparison: pd.DataFrame):
    if comparison.empty:
        return None

    chart_data = comparison.copy()
    chart_data["Periodo"] = (
        chart_data["Mes"].str[:3] + " " + chart_data["Año"].astype(str)
    )

    fig = go.Figure()
    fig.add_bar(
        x=chart_data["Periodo"],
        y=chart_data["Ventas reales"],
        name="Ventas reales",
    )
    fig.add_scatter(
        x=chart_data["Periodo"],
        y=chart_data["Presupuesto"],
        name="Presupuesto",
        mode="lines+markers",
        line={"width": 3},
        marker={"size": 8},
    )
    fig.update_layout(
        title="Presupuesto frente a ventas reales",
        height=460,
        hovermode="x unified",
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "center",
            "x": 0.5,
        },
        margin={"l": 20, "r": 20, "t": 85, "b": 40},
    )
    fig.update_xaxes(title="Mes")
    fig.update_yaxes(
        title="Valor",
        tickprefix="$",
        tickformat="~s",
        rangemode="tozero",
    )
    return fig


def filtered_base_without_dates(
    prepared: pd.DataFrame,
    statuses,
    warehouses,
    clients,
    references,
    products,
    exclude_freight,
) -> pd.DataFrame:
    result = prepared.copy()
    if statuses:
        result = result[result["EstadoNormalizado"].isin(statuses)]
    if warehouses:
        result = result[result["Bodega"].isin(warehouses)]
    if clients:
        result = result[result["Cliente"].isin(clients)]
    if references:
        result = result[result["Referencia"].isin(references)]
    if products:
        result = result[result["Producto"].isin(products)]
    if exclude_freight:
        result = result[~result["EsFlete"]]
    return result


st.title("📊 Aplicación de análisis de ventas")
st.caption(
    "Sube un archivo Excel, filtra por año, mes, cliente, producto o ISBN "
    "y analiza el comportamiento diario, mensual e interanual."
)

uploaded = st.file_uploader(
    "Subir archivo de ventas",
    type=["xlsx", "xls"],
    help="La aplicación no almacena el archivo. El análisis se realiza durante la sesión.",
)

if uploaded is None:
    st.info(
        "Carga un archivo Excel para iniciar. La aplicación reconoce automáticamente "
        "la estructura de Libro1.xlsx y permite corregir el mapeo de columnas."
    )
    st.stop()

file_bytes = uploaded.getvalue()
sheet_names = get_sheet_names(file_bytes)
sheet = st.selectbox("Hoja de datos", sheet_names, index=0)
raw = read_excel_file(file_bytes, sheet)

st.success(f"Archivo cargado: {len(raw):,} filas y {len(raw.columns)} columnas.")

detected = detect_columns(raw.columns)
column_options = ["— No disponible —"] + [str(c) for c in raw.columns]

with st.expander("Configuración de columnas", expanded=False):
    st.write(
        "La aplicación detectó las columnas automáticamente. Ajusta cualquier campo "
        "que no corresponda."
    )
    mapping = {}
    cols = st.columns(3)
    for index, (field, label) in enumerate(CANONICAL_FIELDS.items()):
        detected_col = detected.get(field)
        default_index = (
            column_options.index(detected_col)
            if detected_col in column_options
            else 0
        )
        selected = cols[index % 3].selectbox(
            label,
            column_options,
            index=default_index,
            key=f"map_{field}",
        )
        mapping[field] = None if selected == "— No disponible —" else selected

try:
    prepared = prepare_data(raw, mapping)
except ValueError as error:
    st.error(str(error))
    st.stop()

valid_dates = prepared["Fecha"].dropna()
if valid_dates.empty:
    st.error("No se encontraron fechas válidas.")
    st.stop()

min_date = valid_dates.min().date()
max_date = valid_dates.max().date()

source_months = pd.period_range(
    valid_dates.min().to_period("M"),
    valid_dates.max().to_period("M"),
    freq="M",
)
observed_source_months = set(valid_dates.dt.to_period("M").unique())
missing_source_months = [
    period for period in source_months if period not in observed_source_months
]

MONTH_NAMES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}
MONTH_SHORT = {
    1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic",
}

CORPORATE_BUDGETS = {
    2026: {
        1: 43242750,
        2: 49420286,
        3: 49420286,
        4: 49420286,
        5: 49420286,
        6: 49420286,
        7: 49420286,
        8: 49420286,
        9: 49420286,
        10: 61775357,
        11: 61775357,
        12: 55597821,
    }
}

if missing_source_months:
    missing_labels = ", ".join(
        f"{MONTH_NAMES[period.month]} de {period.year}"
        for period in missing_source_months
    )
    st.warning(
        "Cobertura incompleta en el archivo: no existen filas para "
        f"{missing_labels}. Estos meses se muestran como vacíos para no "
        "confundir ausencia de información con ventas reales de cero."
    )

if min_date.day != 1 or max_date.day < 28:
    st.info(
        f"Cobertura del archivo: {min_date:%d/%m/%Y} a {max_date:%d/%m/%Y}. "
        "El primer o el último mes pueden ser periodos parciales."
    )

with st.sidebar:
    st.header("Filtros")

    available_years = sorted(
        int(year) for year in prepared["Año"].dropna().unique()
    )
    selected_years = st.multiselect(
        "Año",
        available_years,
        default=available_years,
        help="Selecciona uno o varios años para comparar sus meses.",
    )

    available_month_numbers = list(range(1, 13))
    available_month_names = [MONTH_NAMES[month] for month in available_month_numbers]
    selected_month_names = st.multiselect(
        "Mes",
        available_month_names,
        default=available_month_names,
        help=(
            "Se muestran los doce meses, incluso cuando el archivo no contiene "
            "registros para alguno de ellos."
        ),
    )
    selected_month_numbers = [
        month for month, name in MONTH_NAMES.items() if name in selected_month_names
    ]

    date_range = st.date_input(
        "Rango de fechas",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
        help="Permite afinar el análisis dentro de los años y meses seleccionados.",
    )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date = end_date = date_range

    statuses = sorted(x for x in prepared["EstadoNormalizado"].dropna().unique() if x)
    default_status = ["Aprobada"] if "Aprobada" in statuses else statuses
    selected_statuses = st.multiselect("Estado", statuses, default=default_status)

    warehouses = sorted(x for x in prepared["Bodega"].dropna().unique() if x)
    selected_warehouses = st.multiselect("Bodega", warehouses)

    clients = sorted(x for x in prepared["Cliente"].dropna().unique() if x)
    selected_clients = st.multiselect("Cliente", clients)

    references = sorted(
        str(x).strip()
        for x in prepared["Referencia"].dropna().unique()
        if str(x).strip()
    )
    selected_references = st.multiselect(
        "Referencia / ISBN",
        references,
        help=(
            "Escribe el ISBN o código de referencia para localizarlo. "
            "El filtro actualiza todos los indicadores y gráficos."
        ),
        placeholder="Buscar ISBN o referencia",
    )

    products_for_filter = prepared.copy()
    if selected_references:
        products_for_filter = products_for_filter[
            products_for_filter["Referencia"].isin(selected_references)
        ]
    products = sorted(
        x for x in products_for_filter["Producto"].dropna().unique() if x
    )
    selected_products = st.multiselect("Producto", products)

    exclude_freight = st.checkbox("Excluir SERVICIO FLETE", value=True)
    trend_mode = st.radio(
        "Vista de tendencia",
        ["Ventas mensuales", "Ventas acumuladas"],
        horizontal=True,
        help=(
            "La comparación usa enero a diciembre en el eje horizontal "
            "y una línea independiente por cada año."
        ),
    )
    missing_month_mode = st.radio(
        "Meses sin registros",
        ["Mantener vacío", "Mostrar como cero"],
        horizontal=True,
        help=(
            "Mantener vacío evita interpretar una ausencia de datos como una venta "
            "real de cero. Mostrar como cero solo afecta meses históricos dentro de "
            "la cobertura del archivo."
        ),
    )
    top_n = st.slider("Cantidad en rankings", 5, 30, 10)

filtered = apply_dimension_filters(
    prepared,
    start_date,
    end_date,
    selected_statuses,
    selected_warehouses,
    selected_clients,
    selected_products,
    exclude_freight,
)

if selected_references:
    filtered = filtered[filtered["Referencia"].isin(selected_references)]

budget_sales_base = apply_dimension_filters(
    prepared,
    start_date,
    end_date,
    selected_statuses,
    [],
    [],
    [],
    exclude_freight,
)

if selected_years:
    filtered = filtered[filtered["Año"].isin(selected_years)]
    budget_sales_base = budget_sales_base[
        budget_sales_base["Año"].isin(selected_years)
    ]
else:
    filtered = filtered.iloc[0:0]
    budget_sales_base = budget_sales_base.iloc[0:0]

if selected_month_numbers:
    filtered = filtered[filtered["MesNumero"].isin(selected_month_numbers)]
    budget_sales_base = budget_sales_base[
        budget_sales_base["MesNumero"].isin(selected_month_numbers)
    ]
else:
    filtered = filtered.iloc[0:0]
    budget_sales_base = budget_sales_base.iloc[0:0]

base_dimensions = filtered_base_without_dates(
    prepared,
    selected_statuses,
    selected_warehouses,
    selected_clients,
    selected_references,
    selected_products,
    exclude_freight,
)

comparison_start = (
    filtered["Fecha"].min().date()
    if not filtered.empty and filtered["Fecha"].notna().any()
    else start_date
)
comparison_end = (
    filtered["Fecha"].max().date()
    if not filtered.empty and filtered["Fecha"].notna().any()
    else end_date
)

previous_month = comparison_window(
    base_dimensions,
    comparison_start,
    comparison_end,
    pd.DateOffset(months=1),
)
previous_year = comparison_window(
    base_dimensions,
    comparison_start,
    comparison_end,
    pd.DateOffset(years=1),
)

metrics = calculate_metrics(filtered)
metrics_pm = calculate_metrics(previous_month)
metrics_py = calculate_metrics(previous_year)

mom = (
    change_pct(metrics.sales, metrics_pm.sales)
    if len(selected_years) == 1 and len(selected_month_numbers) == 1
    else None
)
yoy = (
    change_pct(metrics.sales, metrics_py.sales)
    if len(selected_years) == 1
    else None
)

st.markdown(
    f'<p class="small-note">Periodo analizado: {start_date:%d/%m/%Y} a '
    f'{end_date:%d/%m/%Y} · {len(filtered):,} líneas filtradas.</p>',
    unsafe_allow_html=True,
)

k1, k2, k3, k4, k5, k6, k7 = st.columns(7)
sales_delta = f"{mom:+.2%} vs mes anterior" if mom is not None else None
k1.metric("Ventas", money(metrics.sales), sales_delta)
k2.metric("Pedidos", number(metrics.orders))
k3.metric("Clientes", number(metrics.clients))
k4.metric("Productos", number(metrics.products))
k5.metric("Unidades", number(metrics.units))
k6.metric("Ticket promedio", money(metrics.ticket))
k7.metric("Variación interanual", delta_text(yoy))

tab_exec, tab_clients, tab_products, tab_trends, tab_quality = st.tabs(
    [
        "Dashboard ejecutivo",
        "Clientes",
        "Productos",
        "Tendencias",
        "Calidad de datos",
    ]
)

with tab_exec:
    budget_comparison = build_budget_comparison(
        budget_sales_base,
        selected_years,
        selected_month_numbers,
        start_date,
        end_date,
        max_date,
    )

    if not budget_comparison.empty:
        st.subheader("Cumplimiento del presupuesto comercial")

        configured_years = sorted(budget_comparison["Año"].unique())
        annual_budget = sum(
            sum(CORPORATE_BUDGETS[year].values())
            for year in configured_years
            if year in CORPORATE_BUDGETS
        )

        available_rows = budget_comparison[~budget_comparison["Es futuro"]]
        comparable_budget = available_rows["Presupuesto"].sum()
        comparable_sales = available_rows["Ventas reales"].fillna(0).sum()
        compliance = (
            comparable_sales / comparable_budget
            if comparable_budget
            else None
        )
        gap = comparable_sales - comparable_budget

        b1, b2, b3, b4, b5 = st.columns(5)
        b1.metric("Presupuesto anual configurado", money(annual_budget))
        b2.metric("Presupuesto periodo disponible", money(comparable_budget))
        b3.metric("Ventas reales del periodo", money(comparable_sales))
        b4.metric(
            "Cumplimiento",
            f"{compliance:.1%}" if compliance is not None else "Sin base",
        )
        b5.metric("Brecha", money(gap))

        if any(
            [
                selected_warehouses,
                selected_clients,
                selected_references,
                selected_products,
            ]
        ):
            st.info(
                "El presupuesto es corporativo y no está distribuido por bodega, "
                "cliente, producto o ISBN. Este bloque conserva el total general "
                "y responde a Año, Mes, rango de fechas, Estado y flete."
            )

        budget_chart = build_budget_chart(budget_comparison)
        if budget_chart is not None:
            st.plotly_chart(budget_chart, use_container_width=True)

        budget_table = budget_comparison[
            [
                "Año",
                "MesNumero",
                "Mes",
                "Presupuesto",
                "Ventas reales",
                "Diferencia",
                "Cumplimiento %",
                "Estado",
            ]
        ].sort_values(["Año", "MesNumero"])

        st.dataframe(
            budget_table.drop(columns=["MesNumero"]),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Presupuesto": st.column_config.NumberColumn(
                    "Presupuesto", format="$ %.0f"
                ),
                "Ventas reales": st.column_config.NumberColumn(
                    "Ventas reales", format="$ %.0f"
                ),
                "Diferencia": st.column_config.NumberColumn(
                    "Diferencia", format="$ %.0f"
                ),
                "Cumplimiento %": st.column_config.NumberColumn(
                    "Cumplimiento %", format="%.1f%%"
                ),
            },
        )
        st.caption(
            "Los meses posteriores a la última fecha cargada aparecen como "
            "Pendiente. El mes en curso se identifica como periodo parcial."
        )
        st.divider()
    elif any(year in CORPORATE_BUDGETS for year in selected_years):
        st.info("No hay meses presupuestales dentro del periodo seleccionado.")
    else:
        st.info(
            "No hay presupuesto configurado para los años seleccionados. "
            "Actualmente está disponible el presupuesto corporativo de 2026."
        )

    executive_chart = build_year_comparison_chart(
        filtered,
        mode=trend_mode,
        title=(
            "Comparativo mensual de ventas por año"
            if trend_mode == "Ventas mensuales"
            else "Ventas acumuladas comparadas por año"
        ),
        missing_mode=missing_month_mode,
    )

    if executive_chart is None:
        st.info("No hay información disponible para construir la tendencia.")
    else:
        st.plotly_chart(executive_chart, use_container_width=True)
        st.caption(
            "Cada línea representa un año. Los meses sin información permanecen "
            "vacíos y no se conectan con otros periodos."
        )

    summary_left, summary_right = st.columns([1, 1.35])

    with summary_left:
        warehouse = (
            filtered.groupby("Bodega", as_index=False)["ValorVenta"]
            .sum()
            .sort_values("ValorVenta", ascending=False)
        )
        if warehouse.empty:
            st.info("No hay datos por bodega para los filtros seleccionados.")
        else:
            fig = px.pie(
                warehouse,
                names="Bodega",
                values="ValorVenta",
                hole=0.45,
                title="Participación por bodega",
            )
            fig.update_layout(
                height=390,
                margin={"l": 10, "r": 10, "t": 60, "b": 20},
                legend={
                    "orientation": "h",
                    "yanchor": "top",
                    "y": -0.05,
                    "xanchor": "center",
                    "x": 0.5,
                },
            )
            st.plotly_chart(fig, use_container_width=True)

    with summary_right:
        monthly_table = prepare_year_month_trend(
            filtered,
            fill_historical_gaps=missing_month_mode == "Mostrar como cero",
        )
        monthly_table = monthly_table[
            monthly_table["ValorVenta"].notna()
        ].copy()
        if monthly_table.empty:
            st.info("No hay valores mensuales para mostrar.")
        else:
            pivot = monthly_table.pivot(
                index="MesNumeroComparacion",
                columns="AñoComparacion",
                values="ValorVenta",
            )
            pivot = pivot.reindex(range(1, 13))
            pivot.index = [MONTH_SHORT[month] for month in range(1, 13)]
            pivot.index.name = "Mes"
            st.subheader("Ventas mensuales por año")
            st.dataframe(
                pivot.style.format("${:,.0f}", na_rep="Sin registros"),
                use_container_width=True,
                height=390,
            )

    c1, c2 = st.columns(2)

    with c1:
        top_clients = (
            filtered.groupby("Cliente", as_index=False)["ValorVenta"]
            .sum()
            .sort_values("ValorVenta", ascending=False)
            .head(top_n)
            .sort_values("ValorVenta")
        )
        fig = px.bar(
            top_clients,
            x="ValorVenta",
            y="Cliente",
            orientation="h",
            title=f"Top {top_n} clientes",
        )
        fig.update_xaxes(tickprefix="$", tickformat="~s")
        fig.update_layout(height=430, margin={"l": 10, "r": 10, "t": 60, "b": 30})
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        top_products = (
            filtered.groupby("Producto", as_index=False)["ValorVenta"]
            .sum()
            .sort_values("ValorVenta", ascending=False)
            .head(top_n)
            .sort_values("ValorVenta")
        )
        fig = px.bar(
            top_products,
            x="ValorVenta",
            y="Producto",
            orientation="h",
            title=f"Top {top_n} productos",
        )
        fig.update_xaxes(tickprefix="$", tickformat="~s")
        fig.update_layout(height=430, margin={"l": 10, "r": 10, "t": 60, "b": 30})
        st.plotly_chart(fig, use_container_width=True)

with tab_clients:
    client_summary = (
        filtered.groupby("Cliente", as_index=False)
        .agg(
            Ventas=("ValorVenta", "sum"),
            Pedidos=("Documento", "nunique"),
            Unidades=("Cantidad", "sum"),
            UltimaCompra=("Fecha", "max"),
        )
    )
    client_summary["TicketPromedio"] = (
        client_summary["Ventas"] / client_summary["Pedidos"].replace(0, pd.NA)
    )
    client_summary = client_summary.sort_values("Ventas", ascending=False)
    client_summary["Participacion"] = (
        client_summary["Ventas"] / client_summary["Ventas"].sum()
        if client_summary["Ventas"].sum()
        else 0
    )

    left, right = st.columns([1.4, 1])
    with left:
        fig = px.bar(
            client_summary.head(top_n).sort_values("Ventas"),
            x="Ventas",
            y="Cliente",
            orientation="h",
            title="Ranking de clientes",
        )
        fig.update_xaxes(tickprefix="$", separatethousands=True)
        st.plotly_chart(fig, use_container_width=True)

    with right:
        pareto = client_summary[["Cliente", "Ventas"]].copy()
        pareto["Acumulado"] = pareto["Ventas"].cumsum()
        total = pareto["Ventas"].sum()
        pareto["AcumuladoPct"] = pareto["Acumulado"] / total if total else 0
        fig = go.Figure()
        fig.add_bar(x=pareto["Cliente"].head(top_n), y=pareto["Ventas"].head(top_n), name="Ventas")
        fig.add_scatter(
            x=pareto["Cliente"].head(top_n),
            y=pareto["AcumuladoPct"].head(top_n),
            name="% acumulado",
            yaxis="y2",
            mode="lines+markers",
        )
        fig.update_layout(
            title="Pareto de clientes",
            yaxis=dict(title="Ventas"),
            yaxis2=dict(title="% acumulado", overlaying="y", side="right", tickformat=".0%"),
            xaxis_tickangle=-45,
        )
        st.plotly_chart(fig, use_container_width=True)

    st.dataframe(
        client_summary,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Ventas": st.column_config.NumberColumn(format="$ %.0f"),
            "TicketPromedio": st.column_config.NumberColumn(format="$ %.0f"),
            "Participacion": st.column_config.NumberColumn(format="%.2f%%"),
            "UltimaCompra": st.column_config.DateColumn(format="DD/MM/YYYY"),
        },
    )

with tab_products:
    if selected_references:
        reference_metrics = calculate_metrics(filtered)
        st.subheader("Resultado de la referencia / ISBN seleccionada")
        ref1, ref2, ref3, ref4 = st.columns(4)
        ref1.metric("Ventas de la referencia", money(reference_metrics.sales))
        ref2.metric("Pedidos", number(reference_metrics.orders))
        ref3.metric("Unidades", number(reference_metrics.units))
        ref4.metric("Clientes", number(reference_metrics.clients))
        st.caption(
            "Referencia(s) seleccionada(s): " + ", ".join(selected_references)
        )

    product_summary = (
        filtered.groupby(["Referencia", "Producto"], as_index=False)
        .agg(
            Ventas=("ValorVenta", "sum"),
            Unidades=("Cantidad", "sum"),
            Pedidos=("Documento", "nunique"),
            Clientes=("Cliente", "nunique"),
        )
        .sort_values("Ventas", ascending=False)
    )
    product_summary["Participacion"] = (
        product_summary["Ventas"] / product_summary["Ventas"].sum()
        if product_summary["Ventas"].sum()
        else 0
    )

    left, right = st.columns(2)
    with left:
        fig = px.bar(
            product_summary.head(top_n).sort_values("Ventas"),
            x="Ventas",
            y="Producto",
            orientation="h",
            title="Productos por valor vendido",
        )
        fig.update_xaxes(tickprefix="$", separatethousands=True)
        st.plotly_chart(fig, use_container_width=True)

    with right:
        units_top = product_summary.sort_values("Unidades", ascending=False).head(top_n)
        fig = px.bar(
            units_top.sort_values("Unidades"),
            x="Unidades",
            y="Producto",
            orientation="h",
            title="Productos por unidades",
        )
        st.plotly_chart(fig, use_container_width=True)

    st.dataframe(
        product_summary,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Referencia": st.column_config.TextColumn(
                "Referencia / ISBN",
                help="Código de referencia o ISBN del producto",
            ),
            "Ventas": st.column_config.NumberColumn(format="$ %.0f"),
            "Participacion": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )

with tab_trends:
    st.subheader("Análisis de tendencia interanual")
    st.caption(
        "La visual compara el mismo mes entre diferentes años. "
        "Esta estructura evita unir fechas de años distintos y facilita identificar "
        "crecimiento, estacionalidad y desaceleración."
    )

    trend_chart = build_year_comparison_chart(
        filtered,
        mode=trend_mode,
        title=(
            "Evolución mensual de ventas por año"
            if trend_mode == "Ventas mensuales"
            else "Evolución acumulada de ventas por año"
        ),
        missing_mode=missing_month_mode,
    )

    if trend_chart is None:
        st.info("No hay información disponible para construir las tendencias.")
    else:
        st.plotly_chart(trend_chart, use_container_width=True)

        comparison_data = prepare_year_month_trend(
            filtered,
            fill_historical_gaps=missing_month_mode == "Mostrar como cero",
        )
        value_column = (
            "VentaAcumulada"
            if trend_mode == "Ventas acumuladas"
            else "ValorVenta"
        )
        table_data = comparison_data[
            comparison_data[value_column].notna()
        ].copy()

        if not table_data.empty:
            comparison_pivot = table_data.pivot(
                index="MesNumeroComparacion",
                columns="AñoComparacion",
                values=value_column,
            )
            comparison_pivot = comparison_pivot.reindex(range(1, 13))
            comparison_pivot.index = [
                MONTH_SHORT[month] for month in range(1, 13)
            ]
            comparison_pivot.index.name = "Mes"

            st.subheader(
                "Matriz de ventas acumuladas"
                if trend_mode == "Ventas acumuladas"
                else "Matriz de ventas mensuales"
            )
            st.dataframe(
                comparison_pivot.style.format("${:,.0f}", na_rep="Sin registros"),
                use_container_width=True,
            )

        weekday = (
            filtered.groupby(
                ["DiaSemanaNumero", "DiaSemana"], as_index=False
            )["ValorVenta"]
            .sum()
            .sort_values("DiaSemanaNumero")
        )
        if not weekday.empty:
            fig = px.bar(
                weekday,
                x="DiaSemana",
                y="ValorVenta",
                title="Ventas por día de la semana",
            )
            fig.update_yaxes(tickprefix="$", tickformat="~s")
            fig.update_layout(height=390)
            st.plotly_chart(fig, use_container_width=True)

with tab_quality:
    st.subheader("Cobertura mensual de la fuente")
    coverage = prepared.dropna(subset=["Fecha"]).copy()
    coverage["AñoMesCobertura"] = coverage["Fecha"].dt.to_period("M")
    coverage_summary = (
        coverage.groupby("AñoMesCobertura", as_index=False)
        .agg(
            Filas=("Documento", "size"),
            VentasRegistradas=("ValorVenta", "sum"),
            FechaMinima=("Fecha", "min"),
            FechaMaxima=("Fecha", "max"),
        )
    )

    complete_periods = pd.DataFrame({
        "AñoMesCobertura": pd.period_range(
            prepared["Fecha"].min().to_period("M"),
            prepared["Fecha"].max().to_period("M"),
            freq="M",
        )
    })
    coverage_summary = complete_periods.merge(
        coverage_summary,
        on="AñoMesCobertura",
        how="left",
    )
    coverage_summary["Estado"] = coverage_summary["Filas"].apply(
        lambda value: "Sin registros" if pd.isna(value) else "Con registros"
    )
    coverage_summary["Periodo"] = coverage_summary["AñoMesCobertura"].astype(str)

    st.dataframe(
        coverage_summary[
            [
                "Periodo",
                "Estado",
                "Filas",
                "VentasRegistradas",
                "FechaMinima",
                "FechaMaxima",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        column_config={
            "VentasRegistradas": st.column_config.NumberColumn(
                "Ventas registradas",
                format="$ %.0f",
            ),
            "FechaMinima": st.column_config.DateColumn(
                "Primera fecha",
                format="DD/MM/YYYY",
            ),
            "FechaMaxima": st.column_config.DateColumn(
                "Última fecha",
                format="DD/MM/YYYY",
            ),
        },
    )

    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Fechas inválidas", int(prepared["Fecha"].isna().sum()))
    q2.metric("Valores inválidos", int(prepared["ValorVenta"].isna().sum()))
    q3.metric("Valores negativos", int((prepared["ValorVenta"] < 0).sum()))
    q4.metric("Posibles duplicados", int(prepared["PosibleDuplicado"].sum()))

    quality_rows = prepared[
        prepared["ObservacionCalidad"].ne("") | prepared["PosibleDuplicado"]
    ].copy()
    st.dataframe(
        quality_rows[
            [
                "Documento", "Fecha", "Estado", "Cliente", "Bodega",
                "Referencia", "Producto", "Cantidad", "ValorVenta",
                "PosibleDuplicado", "ObservacionCalidad",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )

st.divider()
st.subheader("Exportar resultado filtrado")
d1, d2 = st.columns(2)
csv_bytes = filtered.to_csv(index=False).encode("utf-8-sig")
d1.download_button(
    "Descargar CSV",
    data=csv_bytes,
    file_name="ventas_filtradas.csv",
    mime="text/csv",
    use_container_width=True,
)
d2.download_button(
    "Descargar Excel",
    data=to_excel_bytes(filtered),
    file_name="ventas_filtradas.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
)
