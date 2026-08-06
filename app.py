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

MONTH_NAMES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}
MONTH_SHORT = {
    1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic",
}

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

    months_in_selected_years = prepared.copy()
    if selected_years:
        months_in_selected_years = months_in_selected_years[
            months_in_selected_years["Año"].isin(selected_years)
        ]
    available_month_numbers = sorted(
        int(month)
        for month in months_in_selected_years["MesNumero"].dropna().unique()
    )
    available_month_names = [MONTH_NAMES[month] for month in available_month_numbers]
    selected_month_names = st.multiselect(
        "Mes",
        available_month_names,
        default=available_month_names,
        help="El filtro conserva el mismo mes para todos los años seleccionados.",
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
    granularity = st.radio(
        "Vista de tendencia",
        ["Día", "Mes", "Comparativo anual"],
        horizontal=True,
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

if selected_years:
    filtered = filtered[filtered["Año"].isin(selected_years)]
if selected_month_numbers:
    filtered = filtered[filtered["MesNumero"].isin(selected_month_numbers)]

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
    left, right = st.columns([1.7, 1])

    trend = filtered.dropna(subset=["Fecha"]).copy()

    with left:
        if granularity == "Comparativo anual":
            trend["AñoComparacion"] = trend["Fecha"].dt.year.astype(str)
            trend["MesNumeroComparacion"] = trend["Fecha"].dt.month
            trend["MesComparacion"] = trend["MesNumeroComparacion"].map(MONTH_SHORT)
            trend = (
                trend.groupby(
                    ["AñoComparacion", "MesNumeroComparacion", "MesComparacion"],
                    as_index=False,
                )["ValorVenta"]
                .sum()
                .sort_values(["AñoComparacion", "MesNumeroComparacion"])
            )
            fig = px.line(
                trend,
                x="MesNumeroComparacion",
                y="ValorVenta",
                color="AñoComparacion",
                markers=True,
                hover_name="MesComparacion",
                title="Comparativo año a año por mes",
                labels={"AñoComparacion": "Año"},
            )
            fig.update_xaxes(
                tickmode="array",
                tickvals=list(range(1, 13)),
                ticktext=[MONTH_SHORT[month] for month in range(1, 13)],
                title="Mes",
            )
        else:
            if granularity == "Día":
                trend["Periodo"] = trend["Fecha"]
            else:
                trend["Periodo"] = trend["Fecha"].dt.to_period("M").dt.to_timestamp()

            trend = trend.groupby("Periodo", as_index=False)["ValorVenta"].sum()
            fig = px.line(
                trend,
                x="Periodo",
                y="ValorVenta",
                markers=True,
                title=f"Tendencia de ventas por {granularity.lower()}",
            )
            fig.update_xaxes(title="Periodo")

        fig.update_layout(
            yaxis_title="Ventas",
            hovermode="x unified",
            legend_title_text="Año",
        )
        fig.update_yaxes(tickprefix="$", separatethousands=True)
        st.plotly_chart(fig, use_container_width=True)

    with right:
        warehouse = (
            filtered.groupby("Bodega", as_index=False)["ValorVenta"]
            .sum()
            .sort_values("ValorVenta", ascending=False)
        )
        fig = px.pie(
            warehouse,
            names="Bodega",
            values="ValorVenta",
            hole=0.45,
            title="Participación por bodega",
        )
        st.plotly_chart(fig, use_container_width=True)

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
        fig.update_xaxes(tickprefix="$", separatethousands=True)
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
        fig.update_xaxes(tickprefix="$", separatethousands=True)
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
    monthly = monthly_series(filtered)

    fig = px.bar(
        monthly,
        x="Periodo",
        y="ValorVenta",
        title="Comportamiento mes a mes",
    )
    fig.update_yaxes(tickprefix="$", separatethousands=True)
    st.plotly_chart(fig, use_container_width=True)

    yoy_source = filtered.dropna(subset=["Fecha"]).copy()
    yoy_source["Año"] = yoy_source["Fecha"].dt.year.astype(str)
    yoy_source["MesNumero"] = yoy_source["Fecha"].dt.month
    yoy_source["Mes"] = yoy_source["Fecha"].dt.strftime("%b")
    yoy_monthly = (
        yoy_source.groupby(["Año", "MesNumero", "Mes"], as_index=False)["ValorVenta"]
        .sum()
        .sort_values(["Año", "MesNumero"])
    )

    fig = px.line(
        yoy_monthly,
        x="MesNumero",
        y="ValorVenta",
        color="Año",
        markers=True,
        hover_name="Mes",
        title="Comparativo año a año por mes",
    )
    fig.update_xaxes(
        tickmode="array",
        tickvals=list(range(1, 13)),
        ticktext=[
            "Ene", "Feb", "Mar", "Abr", "May", "Jun",
            "Jul", "Ago", "Sep", "Oct", "Nov", "Dic",
        ],
        title="Mes",
    )
    fig.update_yaxes(tickprefix="$", separatethousands=True)
    st.plotly_chart(fig, use_container_width=True)

    weekday = (
        filtered.groupby(["DiaSemanaNumero", "DiaSemana"], as_index=False)["ValorVenta"]
        .sum()
        .sort_values("DiaSemanaNumero")
    )
    fig = px.bar(
        weekday,
        x="DiaSemana",
        y="ValorVenta",
        title="Ventas por día de la semana",
    )
    fig.update_yaxes(tickprefix="$", separatethousands=True)
    st.plotly_chart(fig, use_container_width=True)

with tab_quality:
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
