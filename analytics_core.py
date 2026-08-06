from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import re
import unicodedata
from typing import Dict, Iterable, Mapping, Optional, Tuple

import numpy as np
import pandas as pd


CANONICAL_FIELDS = {
    "document": "Documento",
    "date": "Fecha",
    "status": "Estado",
    "client": "Cliente",
    "warehouse": "Bodega",
    "reference": "Referencia",
    "product": "Producto",
    "quantity": "Cantidad",
    "sales": "ValorVenta",
}

ALIASES = {
    "document": [
        "nro documento", "numero documento", "número documento", "factura",
        "pedido", "documento", "invoice", "order id", "order"
    ],
    "date": [
        "fecha", "fecha factura", "fecha venta", "date", "order date"
    ],
    "status": [
        "estado", "status", "estado pedido", "estado factura"
    ],
    "client": [
        "razón social cliente factura", "razon social cliente factura",
        "cliente", "customer", "nombre cliente"
    ],
    "warehouse": [
        "bodega", "almacen", "almacén", "warehouse", "sucursal", "punto de venta"
    ],
    "reference": [
        "referencia", "sku", "codigo producto", "código producto", "product id"
    ],
    "product": [
        "desc item", "desc. item", "descripcion item", "descripción item",
        "producto", "product", "nombre producto"
    ],
    "quantity": [
        "cantidad inv", "cantidad inv.", "cantidad", "unidades", "qty", "quantity"
    ],
    "sales": [
        "valor subtotal local", "valor venta", "ventas", "subtotal",
        "importe", "revenue", "sales", "total"
    ],
}


@dataclass
class Metrics:
    sales: float
    orders: int
    clients: int
    products: int
    units: float
    ticket: float


def normalize_name(value: object) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def detect_columns(columns: Iterable[object]) -> Dict[str, Optional[str]]:
    original = [str(col) for col in columns]
    normalized = {normalize_name(col): col for col in original}
    detected: Dict[str, Optional[str]] = {}

    for field, aliases in ALIASES.items():
        match = None
        for alias in aliases:
            norm_alias = normalize_name(alias)
            if norm_alias in normalized:
                match = normalized[norm_alias]
                break

        if match is None:
            for col in original:
                norm_col = normalize_name(col)
                if any(normalize_name(alias) in norm_col for alias in aliases):
                    match = col
                    break

        detected[field] = match

    return detected


def _parse_number(value: object) -> float:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)

    text = str(value).strip()
    if not text:
        return np.nan

    negative = text.startswith("(") and text.endswith(")")
    text = text.replace("(", "").replace(")", "")
    text = re.sub(r"[^\d,.\-]", "", text)

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        decimal_part = text.split(",")[-1]
        if len(decimal_part) in (1, 2):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif text.count(".") > 1:
        pieces = text.split(".")
        text = "".join(pieces[:-1]) + "." + pieces[-1]

    try:
        number = float(text)
        return -number if negative else number
    except ValueError:
        return np.nan


def parse_date_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce").dt.normalize()

    numeric = pd.to_numeric(series, errors="coerce")
    numeric_ratio = numeric.notna().mean()

    if numeric_ratio > 0.7:
        parsed = pd.to_datetime(
            numeric, unit="D", origin="1899-12-30", errors="coerce"
        )
    else:
        parsed = pd.to_datetime(series, errors="coerce", dayfirst=True)

    return parsed.dt.normalize()


def prepare_data(
    raw_df: pd.DataFrame,
    mapping: Mapping[str, str],
) -> pd.DataFrame:
    required = ("document", "date", "client", "reference", "product", "quantity", "sales")
    missing = [field for field in required if not mapping.get(field)]
    if missing:
        readable = ", ".join(CANONICAL_FIELDS[field] for field in missing)
        raise ValueError(f"Faltan columnas obligatorias: {readable}")

    df = pd.DataFrame(index=raw_df.index)

    for field, canonical in CANONICAL_FIELDS.items():
        source = mapping.get(field)
        if source and source in raw_df.columns:
            df[canonical] = raw_df[source]
        else:
            df[canonical] = ""

    df["Documento"] = df["Documento"].fillna("").astype(str).str.strip()
    for col in ["Estado", "Cliente", "Bodega", "Referencia", "Producto"]:
        df[col] = (
            df[col]
            .fillna("")
            .astype(str)
            .str.replace(r"\s+", " ", regex=True)
            .str.strip()
        )

    df["Fecha"] = parse_date_series(df["Fecha"])
    df["Cantidad"] = df["Cantidad"].map(_parse_number)
    df["ValorVenta"] = df["ValorVenta"].map(_parse_number)

    status_normalized = df["Estado"].str.casefold()
    df["EstadoNormalizado"] = np.select(
        [
            status_normalized.eq("aprobada"),
            status_normalized.eq("aprobado"),
            status_normalized.eq(""),
        ],
        ["Aprobada", "Aprobada", "Sin estado"],
        default=df["Estado"].replace("", "Sin estado"),
    )

    freight_pattern = r"\bflete\b"
    df["EsFlete"] = (
        df["Producto"].str.contains(freight_pattern, case=False, regex=True, na=False)
        | df["Referencia"].str.contains(freight_pattern, case=False, regex=True, na=False)
    )

    df["VentaNeta"] = np.where(df["EsFlete"], 0.0, df["ValorVenta"])
    df["Año"] = df["Fecha"].dt.year.astype("Int64")
    df["MesNumero"] = df["Fecha"].dt.month.astype("Int64")
    df["Mes"] = df["Fecha"].dt.month_name(locale="C")
    months_es = {
        "January": "Enero", "February": "Febrero", "March": "Marzo",
        "April": "Abril", "May": "Mayo", "June": "Junio",
        "July": "Julio", "August": "Agosto", "September": "Septiembre",
        "October": "Octubre", "November": "Noviembre", "December": "Diciembre",
    }
    df["Mes"] = df["Mes"].map(months_es)
    df["AñoMes"] = df["Fecha"].dt.to_period("M").astype(str)
    df["DiaSemanaNumero"] = (df["Fecha"].dt.dayofweek + 1).astype("Int64")
    weekday_es = {
        1: "Lunes", 2: "Martes", 3: "Miércoles", 4: "Jueves",
        5: "Viernes", 6: "Sábado", 7: "Domingo",
    }
    df["DiaSemana"] = df["DiaSemanaNumero"].map(weekday_es)

    observations = []
    for _, row in df.iterrows():
        issues = []
        if not row["Documento"]:
            issues.append("Documento faltante")
        if pd.isna(row["Fecha"]):
            issues.append("Fecha inválida")
        if not row["Cliente"]:
            issues.append("Cliente faltante")
        if not row["Referencia"]:
            issues.append("Referencia faltante")
        if not row["Producto"]:
            issues.append("Producto faltante")
        if pd.isna(row["Cantidad"]):
            issues.append("Cantidad inválida")
        if pd.isna(row["ValorVenta"]):
            issues.append("Valor inválido")
        if pd.notna(row["ValorVenta"]) and row["ValorVenta"] < 0:
            issues.append("Valor negativo")
        observations.append("; ".join(issues))
    df["ObservacionCalidad"] = observations

    duplicate_subset = ["Documento", "Fecha", "Referencia", "Cantidad", "ValorVenta"]
    df["PosibleDuplicado"] = df.duplicated(subset=duplicate_subset, keep=False)
    return df


def calculate_metrics(df: pd.DataFrame) -> Metrics:
    sales = float(df["ValorVenta"].sum())
    orders = int(df.loc[df["Documento"].ne(""), "Documento"].nunique())
    clients = int(df.loc[df["Cliente"].ne(""), "Cliente"].nunique())
    products = int(df.loc[df["Referencia"].ne(""), "Referencia"].nunique())
    units = float(df["Cantidad"].sum())
    ticket = sales / orders if orders else 0.0
    return Metrics(sales, orders, clients, products, units, ticket)


def apply_dimension_filters(
    df: pd.DataFrame,
    start_date,
    end_date,
    selected_statuses,
    selected_warehouses,
    selected_clients,
    selected_products,
    exclude_freight: bool = True,
) -> pd.DataFrame:
    result = df.copy()
    if start_date is not None:
        result = result[result["Fecha"].dt.date >= start_date]
    if end_date is not None:
        result = result[result["Fecha"].dt.date <= end_date]
    if selected_statuses:
        result = result[result["EstadoNormalizado"].isin(selected_statuses)]
    if selected_warehouses:
        result = result[result["Bodega"].isin(selected_warehouses)]
    if selected_clients:
        result = result[result["Cliente"].isin(selected_clients)]
    if selected_products:
        result = result[result["Producto"].isin(selected_products)]
    if exclude_freight:
        result = result[~result["EsFlete"]]
    return result


def comparison_window(df: pd.DataFrame, start_date, end_date, offset: pd.DateOffset) -> pd.DataFrame:
    start = pd.Timestamp(start_date) - offset
    end = pd.Timestamp(end_date) - offset
    return df[(df["Fecha"] >= start.normalize()) & (df["Fecha"] <= end.normalize())]


def change_pct(current: float, previous: float) -> Optional[float]:
    if previous == 0:
        return None
    return (current / previous) - 1


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Datos filtrados")
    return buffer.getvalue()
