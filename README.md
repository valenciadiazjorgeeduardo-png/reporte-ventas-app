# Aplicación de análisis de ventas

Aplicación web construida con Streamlit para cargar archivos Excel de ventas y analizar:

- Ventas, pedidos, clientes, productos, unidades y ticket promedio.
- Comportamiento diario, mensual y anual.
- Variaciones mes a mes y año a año.
- Top de clientes y productos.
- Participación por bodega.
- Ventas por día de la semana.
- Calidad de datos y posibles duplicados.
- Exportación de los datos filtrados.

## Estructura reconocida

La aplicación reconoce automáticamente la estructura de `Libro1.xlsx`, incluyendo:

- Nro documento
- Fecha
- Estado
- Razón social cliente factura
- Bodega
- Referencia
- Desc. item
- Cantidad inv.
- Valor subtotal local

También permite cambiar manualmente el mapeo de columnas.

## Ejecutar en Windows

1. Instalar Python 3.11 o superior.
2. Descomprimir esta carpeta.
3. Hacer doble clic en `run_app.bat`.

También puede ejecutarse desde una terminal:

```bash
pip install -r requirements.txt
streamlit run app.py
```

La aplicación se abrirá normalmente en:

```text
http://localhost:8501
```

## Ejecutar en macOS o Linux

```bash
chmod +x run_app.sh
./run_app.sh
```

## Publicar en Streamlit Community Cloud

1. Subir estos archivos a un repositorio de GitHub.
2. Crear una aplicación nueva en Streamlit Community Cloud.
3. Seleccionar `app.py` como archivo principal.
4. Implementar.

## Despliegue con Docker

```bash
docker build -t app-ventas .
docker run -p 8501:8501 app-ventas
```

## Reglas predeterminadas

- El estado `Aprobada` queda seleccionado de forma predeterminada.
- `SERVICIO FLETE` se excluye de las ventas, pero puede incluirse desde el filtro.
- Las comparaciones mes a mes y año a año conservan los filtros de cliente, producto, bodega y estado.
- Los posibles duplicados no se eliminan automáticamente.
